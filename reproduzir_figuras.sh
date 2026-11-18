#!/bin/bash
# Regenerates the evaluation figures of the paper from the raw data in this repository (no benchmark needs to run).
#   Fig. 3     latency x throughput without cross-channel transactions, 1/4/6 channels
#   Fig. 4, 5  latency x throughput with 4 and 6 channels, 5/10/20% cross-channel, 2 and 3 destinations
#   Fig. 6     WAN: map of the 4 regions, latency x throughput (10% cross-channel), CDF of Skeen-BFT per region pair
#   extra      Fabric x CometBFT (cross-platform prototype): latency x throughput under controlled load
# Each panel is written as .pdf and .png, as used in the paper: the panel labels ("1 channel", "5%", "WAN configuration",
# "Skeen-BFT") are not drawn; the paper adds them with TikZ (LABELS=1 draws them inside the panels instead).
# previa_fig*.pdf put the three panels of a figure side by side at the paper's text width (needs pymupdf).
# Usage (from the repository root): [LABELS=1] bash reproduzir_figuras.sh [output dir]
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
OUT=${1:-figuras_$(date +%Y%m%d_%H%M%S)}
mkdir -p "$OUT/lan" "$OUT/wan" "$OUT/hetero"
SEM=resultados/2026-10-10_run_semcross_1_4_6shards/brutos
CROSS=resultados/2026-10-08_run_cross_4e6shards/brutos
B5=resultados/2026-10-13_run_bft5nucleos/brutos
WAN=resultados/2026-11-05_run_wan/brutos

echo "== Fig. 3-5 (LAN)"
INV=1; MAPT=""; CDFT=""
[ "${LABELS:-0}" = 1 ] && { INV=""; MAPT="WAN configuration"; CDFT="Skeen-BFT"; }
# ROTULO_PROF + ROTULO_INVISIVEL: same framing as the labeled panels (the y axis may start lower to free the corner
# where the label goes), without the text
FONTE_ARTIGO=1 ROTULO_PROF=1 ROTULO_INVISIVEL=$INV SO_2N3=1 SAIDA="$OUT/lan" $PY resultados/scripts/grafico_151_figuras_lan.py \
    --sem $SEM/seqsemcross_*.txt --cross $CROSS/seqcross46_*.txt --b5 $B5/seqbft5_*.txt > /dev/null

echo "== Fig. 6 (WAN)"
SAIDA="$OUT/wan" MAPA_TITULO="$MAPT" SO_MAPA=1 $PY resultados/scripts/grafico_152_figuras_wan.py \
    --lan $CROSS/seqcross46_1110_1021.txt --wan $WAN/seqwan_1311_1334.txt --pares $WAN/seqwan_1211_1022.txt > /dev/null
# Skeen-CFT with a 50 ms BatchTimeout (runs with suffix _wc); Skeen-BFT without the 2000 clients/channel level
FONTE_ARTIGO=1 SO_SUF="cft:wc" LEGENDA=dentro XLAB="Throughput (ktx/s)" YLAB="Latency (ms)" ROTULOS=0 SEM="bft:2000" \
    RECORTA="cft" $PY resultados/scripts/grafico_wan_unido.py $WAN/seqwan_1711_1043.txt "$OUT/wan/wan.png" 1 > /dev/null
FONTE_ARTIGO=1 ROTULO="$CDFT" $PY resultados/scripts/grafico_cdf_wan.py $WAN/seqwan_1311_1334.txt "$OUT/wan/cdf_bft" \
    --igual --sorteado --legenda "lower right" --sem-titulo --xlab "Latency (ms)" --xmax 2500 > /dev/null

echo "== Fabric x CometBFT"
mkdir -p resultados/graficos
$PY cometbft/grafico_varredura_hetero.py cometbft/resultados/varredura_hetero_20261118.txt | tail -1 | xargs -I{} sh -c 'mv {} "$0/hetero/"; mv "${1%.png}.pdf" "$0/hetero/"' "$OUT" {}

echo "== side-by-side previews"
$PY - "$OUT" <<'PY' || echo "(pymupdf not installed: previews skipped)"
import sys, pymupdf
O = sys.argv[1]
W = 0.325 * 7.16 * 72; GAP = (7.16 * 72 - 3 * W) / 2   # IEEE two-column text width: 7.16 in
def lado(arqs, saida):
    src = [pymupdf.open(a) for a in arqs]
    h = max(W * d[0].rect.height / d[0].rect.width for d in src)
    out = pymupdf.open(); pg = out.new_page(width=7.16 * 72, height=h)
    for i, d in enumerate(src):
        r = d[0].rect; hh = W * r.height / r.width; x = i * (W + GAP)
        pg.show_pdf_page(pymupdf.Rect(x, (h - hh) / 2, x + W, (h + hh) / 2), d, 0)
    out.save(saida, garbage=4, deflate=True); print(saida)
L = f"{O}/lan"
lado([f"{L}/01_no_cross_1shard.pdf", f"{L}/02_no_cross_4shards.pdf", f"{L}/03_no_cross_6shards.pdf"], f"{O}/previa_fig3.pdf")
lado([f"{L}/{n}_4shards_2n3destinations_{p}pct.pdf" for n, p in (("24", "05"), ("25", "10"), ("26", "20"))], f"{O}/previa_fig4.pdf")
lado([f"{L}/{n}_6shards_2n3destinations_{p}pct.pdf" for n, p in (("27", "05"), ("28", "10"), ("29", "20"))], f"{O}/previa_fig5.pdf")
lado([f"{O}/wan/38_channels_map_rtt_pairs.pdf", f"{O}/wan/wan.pdf", f"{O}/wan/cdf_bft_global_igual.pdf"], f"{O}/previa_fig6.pdf")
PY
echo "figures in $OUT/"
