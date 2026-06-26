# Dashboard — Histórico de Fretes (CTEs) · Grupo Minas Port

Dashboard self-contained (HTML único) do custo de frete por **rota** e por **tonelada**,
a partir da aba **TICKETS** da planilha BD - CTE. Mesmo padrão visual dos demais
(tema escuro, verde-limão, logo embutido, Chart.js embutido).

## Arquivos
| Arquivo | Função |
|---|---|
| `index.html` | Dashboard (logo + Chart.js embutidos; busca `dados_ctes.json`). |
| `build_data.py` | Lê a aba TICKETS via gviz CSV e gera `dados_ctes.json` (tratamentos + merge "duas pernas"). |
| `dados_ctes.json` | Dados já processados (snapshot inicial incluído). |
| `.github/workflows/update-ctes.yml` | Roda `build_data.py` a cada 30 min e sob demanda; faz commit do JSON. |
| `manifest.webmanifest` | PWA. |

## Como funciona a atualização automática
1. A planilha precisa estar com **leitura pública** ("Qualquer pessoa com o link" → Leitor).
2. O GitHub Actions executa `build_data.py` a cada 30 min (cron) — também dá pra rodar
   na hora em **Actions → Atualizar dados CTEs → Run workflow**.
3. O script regrava `dados_ctes.json` e faz commit. O `index.html` busca esse JSON ao
   abrir, a cada 30 min, e no botão **Atualizar**.

> O processamento pesado (tratamento de nomes e merge de frete duas pernas) fica no
> Python — o HTML só renderiza. Mesmo padrão da conciliação (`build_data.py` + `index.html`).

## Regras de negócio aplicadas
- **Transportadora**: remove código, estado `(MG)/(RJ)/…`, `MATRIZ/FILIAL`; junta variantes
  da mesma empresa (Rodeiro MG + Rodeiro RJ → **Rodeiro**; Rangel Trans + Rangel Transp → **Rangel**).
- **Cliente**: primeira parte do **Lote** antes do "-" (BELOCAL MTZ, CIM. LIZ, BRASKEM…);
  unifica `*CIM. LIZ / CIMENTOS LIZ → CIM. LIZ`.
- **Frete "duas pernas"** (até março): par com o mesmo `#Agend.` em que uma perna
  termina em Itaúna e a outra sai de Itaúna → consolidado em **1 frete**:
  valor = soma dos 2 CTes; **peso = de 1 CTe**; R$/t = (CTe1+CTe2) / peso de 1.
  Rota mostrada já normalizada: **São João da Barra → cliente**. (205 fretes consolidados.)
- **Cidades**: sem acento e sem sufixo de UF, para juntar grafias diferentes da mesma rota.
- Indicadores sempre **por tonelada** (R$/t).

## Filtros e indicadores
Filtros: **data início/fim, transportadora, cliente, rota**.
Cards: custo médio R$/t · total pago · volume (t) · ticket médio/frete · top transportadora
(por volume) · rota mais cara (R$/t).
Gráficos: evolução do R$/t por mês · volume por transportadora · R$/t por transportadora ·
volume por cliente. Tabela: histórico de R$/t por rota (médio/mín/máx), ordenável.

## Trocar de planilha/aba
Edite no topo de `build_data.py`: `SHEET_ID`, `SHEET_TAB`.

## Deploy
Funciona em **GitHub Pages** (servindo `index.html` + `dados_ctes.json`).
Para acesso privado, use **Cloudflare Pages + Zero Trust Access**, como nos demais.
