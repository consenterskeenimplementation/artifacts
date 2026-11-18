#!/usr/bin/env python3
"""Os três painéis de cima do gráfico wan_lat_vazao (10% cross) num só (12/11/2026): só a
WAN, uma curva por protocolo, com a latência de TODAS as tx (as linhas entre canais e locais de mesma cor juntadas) ×
vazão total. Latência = mediana (p50) do envio no cliente à entrega no último destino (sd_p50), mediana de 3 runs por
nível, a mesma métrica do wan_lat_vazao; o último registro de cada (nível, run) vale (refazimentos).
LAT=avg (padrão desde 12/11/2026, ): média (sd_avg) em vez da mediana, como nos gráficos 01–39;
LAT=p50 reproduz a versão com a mediana.
13/11/2026: SEM="bft:2000 ..." tira níveis (modo:clientes por canal); RECORTA="cft ..." para essas curvas no 1º
nível em que a vazão cai mais de 10% abaixo do máximo já atingido (a regra dos gráficos 30–37; no Skeen-CFT, os níveis
acima de 1000 não se reproduziram na reexecução de 12/11); lê também a bateria extra (pastas _wx: níveis novos no início
do Skeen-BFT e 1500 no Skeen-CFT, rodar_wan_extra.sh). ROTULOS=0 tira os números de clientes por canal (16/11/2026).
Uso (da raiz): python3 resultados/scripts/grafico_wan_unido.py <puxada seqwan_*.txt> <saída.png> [cross: 1 ou 2]"""
import os
import re
import statistics as st
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

EST = {"puro": ("Skeen", "#2ca02c", "^"), "cft": ("Skeen-CFT", "#1f77b4", "o"), "bft": ("Skeen-BFT", "#9467bd", "D")}
CROSS = sys.argv[3] if len(sys.argv) > 3 else "1"
LAT = os.environ.get("LAT", "avg")
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 14, "axes.grid": True, "grid.alpha": 0.3,
                     "legend.fontsize": 11.5, "figure.dpi": 150})
# FONTE_ARTIGO=1 (18/11/2026): painel de 6,4x4,6 pol. com fontes maiores, como as Fig. 3–5 (no artigo cada painel
# ocupa ~0,325 da largura do texto; em 12x8 pol. o texto encolhia para ~3 pt)
FONTE_ARTIGO = os.environ.get("FONTE_ARTIGO") == "1"
if FONTE_ARTIGO:
    plt.rcParams.update({"font.size": 17, "axes.labelsize": 18, "xtick.labelsize": 15.5, "ytick.labelsize": 15.5,
                         "legend.fontsize": 12.5, "legend.handlelength": 1.8, "legend.borderpad": 0.3,
                         "legend.labelspacing": 0.25, "legend.handletextpad": 0.5})

d, cur = {}, None
for l in open(sys.argv[1], errors="replace"):
    if l.startswith("ARQ_"):
        m = re.search(rf"g110w_wan_c0{CROSS}_d2_(\w+?)4_(w[xbc]?)/", l)
        cur = m.group(1) if m else None
        # SO_SUF="cft:wb" (16/11/2026): para esse protocolo, só as pastas com esse sufixo (Skeen-CFT com BatchTimeout de
        # 200 ms, rodar_wan_cft200.sh; "cft:wc" = 50 ms, rodar_wan_cftbt.sh), sem misturar com as de 2 s; os outros protocolos
        # ignoram as pastas _wb e _wc
        so = dict(x.split(":") for x in os.environ.get("SO_SUF", "").split() if ":" in x)
        if m and ((cur in so and m.group(2) != so[cur]) or (cur not in so and m.group(2) in ("wb", "wc"))):
            cur = None
        continue
    if cur and l.startswith("modo="):
        g = lambda k: float(re.search(rf"\b{k}=([\d.]+)", l).group(1))
        d.setdefault(cur, {})[(int(g("workers_por_shard")), int(g("run")))] = (g("d_tps"), g(f"sd_{LAT}"))

fig, ax = plt.subplots(figsize=(6.4, 4.6) if FONTE_ARTIGO else (12, 8))
for m in ("puro", "cft", "bft"):
    runs = d[m]
    lv = sorted({w for w, _ in runs})
    lv = [w for w in lv if f"{m}:{w}" not in os.environ.get("SEM", "").split()]
    pts = [[st.median(runs[(w, r)][k] for r in (1, 2, 3) if (w, r) in runs) for k in range(2)] for w in lv]
    if m in os.environ.get("RECORTA", "").split():
        mx, n = 0.0, len(pts)
        for i, p in enumerate(pts):
            if mx and p[0] < 0.9 * mx:
                n = i
                break
            mx = max(mx, p[0])
        lv, pts = lv[:n], pts[:n]
    rot, cor, mk = EST[m]
    ax.plot([p[0] / 1000 for p in pts], [p[1] for p in pts], color=cor, marker=mk, lw=2.0 if FONTE_ARTIGO else 2.4,
            ms=5.5 if FONTE_ARTIGO else 7, label=rot)
    for w, p in zip(lv, pts) if os.environ.get("ROTULOS", "1") != "0" else []:  # ROTULOS=0: sem os números
        ax.annotate(str(w), (p[0] / 1000, p[1]), textcoords="offset points", xytext=(5, 5), fontsize=9, color="#555555")
ax.set_yscale("log")
if FONTE_ARTIGO:  # 18/11/2026: rótulos 10^n nas décadas, como nas Fig. 3–5; o eixo vai de década inteira a década inteira
    import math
    from matplotlib.ticker import LogFormatterSciNotation
    ys = [y for ln in ax.get_lines() for y in ln.get_ydata()]
    ax.set_ylim(10 ** math.floor(math.log10(min(ys))), 10 ** math.ceil(math.log10(max(ys))))
    ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0,)))
    ax.yaxis.set_major_formatter(LogFormatterSciNotation(base=10))
else:
    ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
ax.yaxis.set_minor_locator(LogLocator(base=10, subs=tuple(range(2, 10))))
ax.yaxis.set_minor_formatter(NullFormatter())
ax.grid(True, which="minor", axis="y", alpha=0.15)
ax.set_xlim(0, None)
ax.set_xlabel(os.environ.get("XLAB", "Throughput (ktx/s)"))  # XLAB: rótulo do eixo x
ax.set_ylabel(os.environ.get("YLAB") or f"Latency, {'mean' if LAT == 'avg' else 'median'} of all transactions (ms)")  # YLAB: rótulo do eixo y
if os.environ.get("LEGENDA") == "dentro":  # 16/11/2026 sem título, legenda dentro, como nos outros gráficos
    ax.legend(loc=os.environ.get("LEG_LOC", "upper right"), frameon=True, framealpha=0.92)
else:
    ax.legend(title=f"WAN, 4 channels, {CROSS}0% cross-channel" + ("" if os.environ.get("ROTULOS", "1") == "0" else "; labels = clients per channel"), title_fontsize=11,
          loc="lower center", bbox_to_anchor=(0.5, 1.01), ncol=3, frameon=False)
fig.savefig(sys.argv[2], bbox_inches="tight")
fig.savefig(sys.argv[2][:-4] + ".pdf", bbox_inches="tight")
print(sys.argv[2])
