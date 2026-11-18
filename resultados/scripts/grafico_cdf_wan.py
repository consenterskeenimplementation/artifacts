#!/usr/bin/env python3
"""CDF da latência do Skeen-BFT na WAN (bateria rodar_wan_cdf.sh, 12/11/2026): 7 curvas, uma para a configuração global
(tx globais com par sorteado entre todas as regiões) e uma para cada par de canais (tx globais só entre o par). Lê as
linhas "CDF tag=... tipo=... n=... q=..." (quantis 0,5%..99,5%, 99,9% e máximo, juntando os 3 runs; cdf_quantis.py).
Dois gráficos: todas as tx e só as globais. Latência = envio no cliente → entrega no último destino.
Opções: --sem-travados lê só a seção seq_wan_cdf_sem.txt (quantis recalculados sem os runs travados, cdf_quantis.py
--descarta-travados); --w N lê as configurações com N clientes/canal (sufixo w<N> das pastas; padrão 100, sem sufixo).
--dentro-global (12/11/2026): as 6 curvas de pares vêm da própria configuração global (tx globais
de cada par dentro dos mesmos runs com par sorteado, cdf_quantis.py --por-par), sob a mesma carga; a curva global é
então a mistura das 6. Só o gráfico das tx globais.
Uso (da raiz): python3 resultados/scripts/grafico_cdf_wan.py <puxada seqwan_*.txt> <prefixo de saída> [--sem-travados] [--w N]
      [--dentro-global] [--config <sufixo> --titulo "<texto>"]
--config (13/11/2026, teste do relógio): com --dentro-global, lê a configuração g110w_wan_c01_d2_bft4_<sufixo> (ex.: relnat,
relrate) em vez de cdfg; --titulo troca o título da legenda.
--global-mistura (13/11/2026): a curva "All regions" deixa de ser a configuração global
(outra carga: tx globais espalhadas pelos 4 canais) e passa a ser a mistura das 6 curvas de pares com peso igual (par
sorteado uniformemente): F(x) = média das 6 CDFs em x. Fica, por construção, entre as curvas dos pares.
--igual (13/11/2026, rodar_wan_cdf_igual.sh): mesma carga em todas as configurações (100 clientes/canal com teto de tx/s);
curvas ig<a><b> (tx globais só entre o par) e "All regions" MEDIDA (igtodas: cada tx global vai aos 4 canais). Só as tx
globais; usa os quantis sem runs travados (CDF_SEM) quando existem. Com --sorteado, "All regions" = configuração igsort
(cada tx global a 2 canais, par sorteado entre todas as regiões, mesma carga), que fica entre os pares.
--sem-titulo tira o título da legenda; --xlab "<texto>" troca o rótulo do eixo x (17/11/2026).
--pref ch: configurações da carga cheia (rodar_wan_cdf_cheia.sh, ch<a><b> e chsort) em vez de ig."""
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FixedLocator, FuncFormatter, LogLocator, NullFormatter

QS = [i / 200 for i in range(1, 200)] + [0.999, 1.0]
# par -> (rótulo, RTT); cada par com uma cor categórica (ordem fixa da paleta validada para daltonismo entre vizinhas,
# 12/11/2026: a rampa de azuis confundia os pares) e um marcador próprio; a global em preto, grossa e tracejada
PARES = {"12": ("São Paulo–N. Virginia", 115), "13": ("São Paulo–Frankfurt", 205), "14": ("São Paulo–Tokyo", 255),
         "23": ("N. Virginia–Frankfurt", 90), "24": ("N. Virginia–Tokyo", 145), "34": ("Frankfurt–Tokyo", 225)}
CORES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"]
MARCAS = ["o", "s", "^", "D", "v", "P"]
TRACOS = ["-", "-", "-", "-", "-", "-."]  # violeta x azul: o par menos distinto, também pelo traço
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 14, "axes.grid": True, "grid.alpha": 0.3,
                     "legend.fontsize": 11, "figure.dpi": 150})
# FONTE_ARTIGO=1 (18/11/2026): painel de 6,4x4,6 pol. com fontes maiores, como as Fig. 3–5 (no artigo cada painel
# ocupa ~0,325 da largura do texto; em 12x8 pol. o texto encolhia para ~3 pt)
FONTE_ARTIGO = os.environ.get("FONTE_ARTIGO") == "1"
if FONTE_ARTIGO:
    plt.rcParams.update({"font.size": 17, "axes.labelsize": 18, "xtick.labelsize": 15.5, "ytick.labelsize": 15.5,
                         "legend.fontsize": 10.5, "legend.handlelength": 1.8, "legend.borderpad": 0.3,
                         "legend.labelspacing": 0.25, "legend.handletextpad": 0.5})

SEM = "--sem-travados" in sys.argv
DENTRO = "--dentro-global" in sys.argv
MISTURA = "--global-mistura" in sys.argv
SORT = "--sorteado" in sys.argv
PREF = sys.argv[sys.argv.index("--pref") + 1] if "--pref" in sys.argv else "ig"  # --igual: ig (1000 tx/s) ou ch (carga cheia)
LEG = sys.argv[sys.argv.index("--legenda") + 1] if "--legenda" in sys.argv else "lower right"  # ex.: "upper left"
W = sys.argv[sys.argv.index("--w") + 1] if "--w" in sys.argv else "100"
SW = "" if W == "100" else f"w{W}"
CFG = sys.argv[sys.argv.index("--config") + 1] if "--config" in sys.argv else f"cdfg{SW}"
TIT = sys.argv[sys.argv.index("--titulo") + 1] if "--titulo" in sys.argv else f"Skeen-BFT, WAN, 4 channels, {W} clients/channel"
cdf, secao, cdf_sem = {}, None, set()
for l in open(sys.argv[1], errors="replace"):
    if l.startswith("ARQ_"):
        secao = l.strip()
        continue
    sem = "seq_wan_cdf_sem.txt" in (secao or "")
    if sem and not SEM:
        continue
    if "--igual" in sys.argv:
        m = re.match(rf"CDF(_SEM)? tag=g110w_wan_c01_d[24]_bft4_{PREF}(\d\d|todas|sort) tipo=(globais) n=(\d+) q=([\d.,]+)", l)
        # --sorteado: a curva "All regions" é a configuração com par sorteado (igsort); senão, a de 4 destinos (igtodas)
        if m and m.group(2) == ("todas" if SORT else "sort"):
            continue
        if m and (m.group(1) or (m.group(2), "globais") not in cdf_sem):
            k = ("g" if m.group(2) in ("todas", "sort") else m.group(2), "globais")
            cdf[k] = (int(m.group(4)), [float(v) for v in m.group(5).split(",")])
            if m.group(1):
                cdf_sem.add((m.group(2), "globais"))
        continue
    if DENTRO:
        m = re.match(rf"CDF tag=g110w_wan_c01_d2_bft4_{CFG}(?:_par(\d\d))? tipo=(globais) n=(\d+) q=([\d.,]+)", l)
        if m:
            cdf[(m.group(1) or "g", "globais")] = (int(m.group(3)), [float(v) for v in m.group(4).split(",")])
        continue
    m = re.match(rf"CDF tag=g110w_wan_c01_d2_bft4_cdf(g|\d\d){SW} tipo=(\w+) n=(\d+) q=([\d.,]+)", l)
    # com --sem-travados, a seção seq_wan_cdf_sem.txt sobrepõe a original; as curvas sem run descartado só aparecem na
    # original, porque a puxada remove linhas repetidas
    if m and (sem or (m.group(1), m.group(2)) not in cdf_sem):
        cdf[(m.group(1), m.group(2))] = (int(m.group(3)), [float(v) for v in m.group(4).split(",")])
        if sem:
            cdf_sem.add((m.group(1), m.group(2)))
assert cdf, "nenhuma linha CDF para essa seleção"

ordem = sorted(PARES, key=lambda p: PARES[p][1])
for tipo, nome in ((("globais", "global_igual"),) if "--igual" in sys.argv else (("globais", "global_por_par"),) if DENTRO else (("todas", "all"), ("globais", "global"))):
    fig, ax = plt.subplots(figsize=(6.4, 4.6) if FONTE_ARTIGO else (12, 8))
    for i, p in enumerate(ordem):
        if (p, tipo) in cdf:
            n, q = cdf[(p, tipo)]
            rot, r = PARES[p]
            ax.plot(q, QS, color=CORES[i], ls=TRACOS[i], lw=1.8 if FONTE_ARTIGO else 2.4, marker=MARCAS[i],
                    ms=4.5 if FONTE_ARTIGO else 7, markevery=(4 + 3 * i, 24),
                    label=f"{rot} (RTT {r} ms)")
    if "--com-mistura" in sys.argv:  # 13/11/2026: além da medida, a mistura das 6 curvas isoladas (sem interferência)
        cs = [cdf[(p, tipo)][1] for p in ordem if (p, tipo) in cdf]
        x = np.unique(np.concatenate(cs))
        f = np.mean([np.interp(x, q, QS, left=0.0, right=1.0) for q in cs], axis=0)
        print(f"MISTURA_ISOLADOS p50={np.interp(0.5, f, x):.0f}")
        ax.plot(x, f, color="#8a8a8a", lw=3.0, ls=":", label="Equal mix of the 6 isolated pairs (no interference)")
    if MISTURA:
        cs = [cdf[(p, tipo)][1] for p in ordem if (p, tipo) in cdf]
        assert len(cs) == 6, "a mistura precisa das 6 curvas de pares"
        x = np.unique(np.concatenate(cs))
        f = np.mean([np.interp(x, q, QS, left=0.0, right=1.0) for q in cs], axis=0)
        print(f"MISTURA tipo={tipo} p50={np.interp(0.5, f, x):.0f} p10={np.interp(0.1, f, x):.0f} p90={np.interp(0.9, f, x):.0f}")
        ax.plot(x, f, color="#1b1b1b", lw=3.2, ls="--", label="All regions (equal mix of the 6 pairs)")
    elif ("g", tipo) in cdf:
        n, q = cdf[("g", tipo)]
        ax.plot(q, QS, color="#1b1b1b", lw=3.2, ls="--", label="All regions (random pair)" if SORT else "All regions (each tx to the 4 channels)" if "--igual" in sys.argv
                else "All pairs together" if DENTRO else "All regions (random pair)")
    ax.set_xscale("log")
    # --igual: faixa estreita (~450–1400 ms): marcas em 500, 600, ..., 1000 para ler as medianas
    hi = float(sys.argv[sys.argv.index("--xmax") + 1]) if "--xmax" in sys.argv else ax.get_xlim()[1]
    marcas = (range(100, 3100, 100) if hi < 1300 else [500, 600, 700, 800, 900, 1000, 1200, 1500] if hi < 1900
              else [500, 600, 800, 1000, 1500, 2000, 3000, 4000, 5000])
    if FONTE_ARTIGO:  # fonte maior: menos marcas, para os números não se sobreporem
        marcas = [500, 700, 1000, 1500, 2000]
    ax.xaxis.set_major_locator(FixedLocator(marcas) if ("--igual" in sys.argv or DENTRO) else LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_ylim(0, 1.0)
    if "--xmax" in sys.argv:  # espaço à direita para a legenda dentro do gráfico (17/11/2026)
        ax.set_xlim(right=float(sys.argv[sys.argv.index("--xmax") + 1]))
    ax.set_xlabel(sys.argv[sys.argv.index("--xlab") + 1] if "--xlab" in sys.argv else "Latency (ms, log scale)")
    ax.set_ylabel(f"CDF ({'all transactions' if tipo == 'todas' else 'cross-channel transactions only'})".replace(" (", "\n(")
                  if FONTE_ARTIGO else f"CDF ({'all transactions' if tipo == 'todas' else 'cross-channel transactions only'})",
                  fontsize=15 if FONTE_ARTIGO else None)
    fora = dict(loc="upper center", bbox_to_anchor=(0.5, -0.11), ncol=2) if LEG == "fora" else dict(loc=LEG)  # "fora": embaixo
    ax.legend(title=None if "--sem-titulo" in sys.argv else TIT, title_fontsize=11, **fora,
              frameon=True, framealpha=0.92, **({"fontsize": 9.5} if FONTE_ARTIGO else {}))
    if os.environ.get("ROTULO"):  # rótulo do painel dentro da figura (antes posto pelo LaTeX), canto superior direito
        ax.text(0.97, 0.93, os.environ["ROTULO"], transform=ax.transAxes, ha="right", va="top", fontsize=24,
                fontweight="bold", family="serif")
    saida = f"{sys.argv[2]}_{nome}.png"
    fig.savefig(saida, bbox_inches="tight")
    fig.savefig(saida[:-4] + ".pdf", bbox_inches="tight")
    plt.close(fig)
    print(saida)
