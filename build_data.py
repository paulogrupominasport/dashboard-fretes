#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_data.py — Grupo Minas Port
Gera dados_ctes.json para o dashboard "Histórico de Fretes (CTEs)".

Lê a aba TICKETS da planilha do Google (endpoint gviz CSV, leitura pública),
aplica o tratamento de nomes, o merge de frete "duas pernas" (São João da
Barra -> Itaúna -> cliente) e grava um JSON com 1 registro por frete.

Executado pelo GitHub Actions a cada 30 min (cron) ou sob demanda.
Sem dependências externas (usa apenas a biblioteca padrão).
"""

import csv
import io
import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# ----------------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------------
SHEET_ID = "1pzjzRHDw1cHNQ4U287mMFIr9iOiyVH5MvvSPGW0yLDE"
SHEET_TAB = "Tickets"
OUTPUT = "dados_ctes.json"

GVIZ_CSV = (
    "https://docs.google.com/spreadsheets/d/{id}/gviz/tq"
    "?tqx=out:csv&sheet={tab}"
)

# Colunas usadas (cabeçalho exato da aba TICKETS)
COL_AGEND   = "#Agend."
COL_EMPRESA = "Empresa"               # transportadora
COL_PESO    = "Peso CTE"              # toneladas (coluna F)
COL_VFINAL  = "Valor Final Pagamento" # R$ (coluna L)
COL_DTEMIS  = "Data Emiss. CTe"       # coluna M
COL_LOTE    = "Lote"                  # coluna S -> cliente
COL_COLETA  = "Cidade Coleta"         # coluna AC
COL_ENTREGA = "Cidade Entrega"        # coluna AD

TZ_BR = timezone(timedelta(hours=-3))  # America/Sao_Paulo (sem horário de verão)


# ----------------------------------------------------------------------------
# Tratamento de nomes
# ----------------------------------------------------------------------------
def clean_carrier(raw):
    """'507 - RODEIRO (MG) MATRIZ' -> ('Rodeiro', 'RODEIRO').
    Retorna (nome_exibicao, chave_de_grupo)."""
    if not raw:
        return ("", "")
    s = str(raw)
    if " - " in s:
        s = s.split(" - ", 1)[1]
    s = s.upper()
    s = re.sub(r"\([^)]*\)", "", s)            # remove (MG), (RJ)...
    s = re.sub(r"\s*-\s*.*$", "", s)           # remove sufixo após "-"
    s = re.sub(r"\b(MG|RJ|SC|ES|SP|BA|MATRIZ|FILIAL|EPP|LTDA|ME)\b", " ", s)
    s = re.sub(r"\.", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    key = re.sub(r"\b(TRANSPORTES|TRANSPORTE|TRANSP|TRANS|LOGISTICA|LOG)\b", " ", s)
    key = re.sub(r"\s+", " ", key).strip()
    return (_title(s), key)


def clean_client(lote):
    """Primeira parte do Lote antes do '-'. Normaliza variações de CIM. LIZ."""
    if not lote:
        return "—"
    s = str(lote).strip()
    part = re.split(r"\s*[-–]\s*", s, 1)[0].strip().upper()
    part = part.lstrip("*").strip()
    if re.search(r"CIM\.?\s*LIZ", part) or part == "CIMENTOS LIZ":
        part = "CIM. LIZ"
    return part or "—"


_BR_UF = "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO"


def _strip_accents(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def clean_city(c):
    if not c:
        return ""
    s = _strip_accents(str(c).strip().upper())
    s = re.sub(r"\([^)]*\)", "", s)                       # remove (MG), (RJ)...
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*[-/]\s*(" + _BR_UF + r")\b", "", s)
    s = re.sub(r"\s+(" + _BR_UF + r")$", "", s)
    return s.strip()


def _title(s):
    small = {"DE", "DA", "DO", "DAS", "DOS", "E"}
    out = []
    for i, w in enumerate(s.split()):
        if any(ch.isdigit() for ch in w):     # G7, DC4 -> mantém caixa alta
            out.append(w.upper())
        elif w in small and i > 0:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


# ----------------------------------------------------------------------------
# Parsing de valores
# ----------------------------------------------------------------------------
def to_float(v):
    if v is None or v == "":
        return 0.0
    s = str(v).strip()
    s = re.sub(r"[^\d,.\-]", "", s)
    if "," in s and "." in s:          # formato BR: 1.234,56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:                     # 1234,56
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def to_date(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day") and not isinstance(v, str):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # gviz às vezes devolve "Date(2026,2,13)"
    m = re.match(r"Date\((\d+),(\d+),(\d+)", s)
    if m:
        y, mo, d = (int(m.group(1)), int(m.group(2)) + 1, int(m.group(3)))
        try:
            return datetime(y, mo, d).date()
        except ValueError:
            return None
    return None


# ----------------------------------------------------------------------------
# Leitura da planilha
# ----------------------------------------------------------------------------
def fetch_rows():
    url = GVIZ_CSV.format(id=SHEET_ID, tab=urllib.parse.quote(SHEET_TAB))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    return list(reader)


# ----------------------------------------------------------------------------
# Construção dos registros (com merge "duas pernas")
# ----------------------------------------------------------------------------
def build(rows):
    groups = defaultdict(list)
    valid = 0
    for r in rows:
        ag = str(r.get(COL_AGEND) or "").strip()
        if not ag:
            continue
        valid += 1
        groups[ag].append(r)

    freights = []
    two_leg = 0
    for ag, rs in groups.items():
        coletas = [clean_city(r.get(COL_COLETA)) for r in rs]
        entregas = [clean_city(r.get(COL_ENTREGA)) for r in rs]
        is_two_leg = (
            len(rs) == 2
            and any("ITAUNA" in e for e in entregas)
            and any("ITAUNA" in c for c in coletas)
        )

        if is_two_leg:
            two_leg += 1
            final_leg = next((r for r, e in zip(rs, entregas) if "ITAUNA" not in e), rs[0])
            origin_leg = next((r for r, c in zip(rs, coletas) if "ITAUNA" not in c), rs[0])
            valor = sum(to_float(r.get(COL_VFINAL)) for r in rs)   # soma os 2 CTEs
            peso = to_float(origin_leg.get(COL_PESO)) or to_float(final_leg.get(COL_PESO))
            disp, key = clean_carrier(final_leg.get(COL_EMPRESA))
            client = clean_client(final_leg.get(COL_LOTE))
            origem = clean_city(origin_leg.get(COL_COLETA))
            destino = clean_city(final_leg.get(COL_ENTREGA))
            dts = [d for d in (to_date(r.get(COL_DTEMIS)) for r in rs) if d]
            data = max(dts) if dts else None
            freights.append(_rec(ag, True, disp, key, client, origem, destino, peso, valor, data))
        else:
            for r in rs:
                disp, key = clean_carrier(r.get(COL_EMPRESA))
                client = clean_client(r.get(COL_LOTE))
                origem = clean_city(r.get(COL_COLETA))
                destino = clean_city(r.get(COL_ENTREGA))
                peso = to_float(r.get(COL_PESO))
                valor = to_float(r.get(COL_VFINAL))
                data = to_date(r.get(COL_DTEMIS))
                freights.append(_rec(ag, False, disp, key, client, origem, destino, peso, valor, data))

    # Nome de exibição canônico por transportadora (1 nome por chave de grupo)
    from collections import Counter
    disp_by_key = defaultdict(Counter)
    for fr in freights:
        if fr["ckey"]:
            disp_by_key[fr["ckey"]][fr["carrier"]] += 1
    canonical = {k: c.most_common(1)[0][0] for k, c in disp_by_key.items()}
    for fr in freights:
        if fr["ckey"] in canonical:
            fr["carrier"] = canonical[fr["ckey"]]

    meta = {
        "generated_at": datetime.now(TZ_BR).strftime("%d/%m/%Y %H:%M"),
        "generated_iso": datetime.now(TZ_BR).isoformat(),
        "tz": "America/Sao_Paulo",
        "source_tab": SHEET_TAB,
        "rows_valid": valid,
        "freights": len(freights),
        "two_leg_merged": two_leg,
    }
    return {"meta": meta, "freights": freights}


def _rec(ag, twoleg, carrier, ckey, client, origem, destino, peso, valor, data):
    origem_d = _title(origem)
    destino_d = _title(destino)
    rpt = round(valor / peso, 2) if peso > 0 else None
    return {
        "ag": ag,
        "tl": twoleg,
        "carrier": carrier,
        "ckey": ckey,
        "client": client,
        "origem": origem_d,
        "destino": destino_d,
        "rota": f"{origem_d} → {destino_d}" if origem_d and destino_d else (origem_d or destino_d or "—"),
        "peso": round(peso, 3),
        "valor": round(valor, 2),
        "rpt": rpt,
        "data": data.isoformat() if data else None,
        "mes": data.strftime("%Y-%m") if data else None,
    }


def main():
    try:
        rows = fetch_rows()
    except Exception as e:  # noqa
        print(f"ERRO ao ler a planilha: {e}", file=sys.stderr)
        sys.exit(1)
    payload = build(rows)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    m = payload["meta"]
    print(f"OK -> {OUTPUT} | fretes={m['freights']} | duas-pernas={m['two_leg_merged']} | {m['generated_at']}")


if __name__ == "__main__":
    import urllib.parse  # noqa  (usado em fetch_rows)
    main()
