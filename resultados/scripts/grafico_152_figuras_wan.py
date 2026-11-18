#!/usr/bin/env python3
"""
Gráfico 152 — teste de WAN (bateria rodar_wan.sh de 10–11/11/2026) no estilo do gráfico 151
(inglês, sem título, Skeen verde / Skeen-CFT azul / Skeen-BFT roxo, .png + .pdf). Pedidos de 12/11/2026:
  - LAN = os valores já apresentados (bateria cross46 de 08–11/10, LAN de 2 ms, mesmos dados dos gráficos 05/06);
  - cada gráfico em duas versões: LAN × WAN e só WAN;
  - um gráfico de vazão (LAN × WAN) e um focado em latência;
  - um gráfico dos pares de destinos sobre o mapa, para o leitor visualizar o globo.
Configuração: 4 canais (um por região), 2 destinos por tx cross-channel, 10% e 20% de tx cross-channel.
Latência = média do envio no cliente à entrega no último destino (sd_avg), mediana dos 3 runs por nível — a mesma
métrica dos gráficos 01–29. Como no 151, a curva para de ser desenhada no 1º nível em que a vazão cai mais de 10%
abaixo do máximo já atingido (colapso por sobrecarga).
  30/31  vazão × clientes por canal, LAN × WAN (10% / 20%)
  32/33  vazão × clientes por canal, só WAN
  34/35  latência × vazão, LAN × WAN
  36/37  latência × vazão, só WAN
  38     mapa: os 4 canais e o RTT de cada par de regiões (matriz lat_ec2.csv)
  39     latência média das tx cross-channel por par de destinos (6 pares = combinações 2 a 2 dos 4 canais), LAN × WAN,
         ordenados pelo RTT; dados da bateria curta rodar_wan_pares.sh (12/11/2026: Skeen e Skeen-CFT com 300
         clientes/canal, Skeen-BFT com 100, 10% cross, 3 runs; LAN = condição controle, RTT 2 ms entre canais)
  39b    o mapa do 38 em cima e o 39 embaixo, numa figura só
Gráficos do mesmo tipo (30–33; 34–37) compartilham a escala dos eixos.
Uso (da raiz): python3 resultados/scripts/grafico_152_figuras_wan.py --lan <seqcross46_*> --wan <seqwan_*> \
      [--pares <puxada da bateria rodar_wan_pares.sh>]   (38 e 39b precisam do cartopy)
"""
import csv
import os
import re
import statistics as st
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter, LogLocator, NullFormatter, NullLocator

PASTA = os.environ.get("SAIDA", f"resultados/graficos/figuras_wan_{time.strftime('%Y%m%d_%H%M%S')}")
MATRIZ = "resultados/wan/lat_ec2.csv"
LINHA = re.compile(r"modo=(\w+) cross=[\d.]+ workers_por_shard=(\d+) run=(\d+) .*?d_tps=(\d+) d_n=(\d+) incompletas_d=(\d+) "
                   r"sd_avg=([\d.]+|NA)ms")
SEC_LAN = re.compile(r"/dev/shm/g109dx_c(\d+)_d2_([a-z]+)4_(p3?)/")
SEC_WAN = re.compile(r"/dev/shm/g110w_wan_c(\d+)_d2_([a-z]+)4_w/")
EST = {"puro": ("Skeen", "#2ca02c", "^"), "cft": ("Skeen-CFT", "#1f77b4", "o"), "bft": ("Skeen-BFT", "#9467bd", "D")}
MODOS = ("puro", "cft", "bft")
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 14, "axes.grid": True, "grid.alpha": 0.3,
                     "legend.fontsize": 11.5, "figure.dpi": 150})

args, atual = {"--lan": [], "--wan": [], "--pares": []}, None
for a in sys.argv[1:]:
    if a in args:
        atual = a
    elif atual:
        args[atual].append(a)


def ler(arqs, sec_re):
    """{(modo, pct, sufixo): {(w, run): r}}; o último registro de cada (nível, run) vale (refazimentos)"""
    out = {}
    for arq in arqs:
        sec = None
        for linha in open(arq, encoding="utf-8", errors="replace"):
            if linha.startswith("ARQ_"):
                m = sec_re.search(linha)
                c = m.group(1) if m else ""
                pct = int(round(float(c[0] + "." + c[1:]) * 100)) if m else None  # c01 = 0,1 = 10%
                sec = (m.group(2), pct, m.group(3) if m.lastindex >= 3 else "w") if m and pct in (10, 20) else None
                continue
            m = LINHA.search(linha)
            if sec and m and m.group(1) == sec[0] and m.group(7) != "NA":
                dn, inc = int(m.group(5)), int(m.group(6))
                out.setdefault(sec, {})[(int(m.group(2)), int(m.group(3)))] = dict(
                    w=int(m.group(2)), tps=float(m.group(4)), lat=float(m.group(7)), inc=inc / max(1, dn + inc))
    return out


def curva(rs):
    return [dict(w=w, tps=st.median(r["tps"] for r in g), lat=st.median(r["lat"] for r in g))
            for w in sorted({r["w"] for r in rs.values()}) for g in [[r for r in rs.values() if r["w"] == w]]]


def recorta(pts, lim=0.90):
    out, maxtps = [], 0.0
    for p in pts:
        if maxtps and p["tps"] < maxtps * lim:
            break
        maxtps = max(maxtps, p["tps"])
        out.append(p)
    return out


lan_bruto = ler(args["--lan"], SEC_LAN)
LAN = {}
for (m, p, suf), rs in lan_bruto.items():  # curva base (_p) + níveis novos da extensão (_p3), como no 151
    if suf != "p":
        continue
    cur = dict(rs)
    ws = {w for w, _ in cur}
    cur.update({k: r for k, r in lan_bruto.get((m, p, "p3"), {}).items() if k[0] not in ws})
    LAN[(m, p)] = recorta(curva(cur))
WAN = {(m, p): recorta(curva(rs)) for (m, p, _), rs in ler(args["--wan"], SEC_WAN).items()}
for m in MODOS:
    for p in (10, 20):
        assert (m, p) in LAN and (m, p) in WAN, f"faltam dados de {m} {p}%"

os.makedirs(PASTA, exist_ok=True)
feitos = []


def salva(fig, nome):
    if os.environ.get("SO") and nome.split("_")[0] not in os.environ["SO"].split(","):
        plt.close(fig)  # SO=38,39b: gera só esses gráficos
        return
    fig.savefig(f"{PASTA}/{nome}.png", bbox_inches="tight")
    fig.savefig(f"{PASTA}/{nome}.pdf", bbox_inches="tight")
    plt.close(fig)
    feitos.append(nome)


def desenha(ax, pts, m, cond, x, y):
    _, cor, mk = EST[m]
    if cond == "lan":
        ax.plot([x(q) for q in pts], [y(q) for q in pts], color=cor, marker=mk, ls="--", lw=2.0, ms=7, mfc="white",
                mew=1.8, alpha=0.85)
    else:
        ax.plot([x(q) for q in pts], [y(q) for q in pts], color=cor, marker=mk, ls="-", lw=2.4, ms=7)


def legenda(ax, conds, loc):
    h = [Line2D([], [], color=EST[m][1], marker=EST[m][2], lw=2.4, ms=7, label=EST[m][0]) for m in MODOS]
    if len(conds) == 2:
        h += [Line2D([], [], color="#444444", ls="-", lw=2.4, label="WAN (RTT 90–255 ms)"),
              Line2D([], [], color="#444444", ls="--", lw=2.0, marker="o", mfc="white", mew=1.8, label="LAN (2 ms)")]
    else:
        h += [Line2D([], [], color="none", label="WAN (RTT 90–255 ms)")]
    ax.legend(handles=h, loc=loc, frameon=True, framealpha=0.92, ncol=2 if len(conds) == 2 else 1)


def mais_ticks_y(ax):
    ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    ax.yaxis.set_minor_locator(LogLocator(base=10, subs=tuple(range(2, 10))))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.grid(True, which="minor", axis="y", alpha=0.15)


def todos(conds):
    return [pts for c in conds for pts in (LAN if c == "lan" else WAN).values()]


# 30–33: vazão × clientes por canal
NIVEIS = (25, 100, 300, 600, 1000, 2000, 4000)
ymax_v = max(q["tps"] for pts in todos(("lan", "wan")) for q in pts) / 1000 * 1.12
num = 30
for conds, sufixo in ((("lan", "wan"), "lan_x_wan"), (("wan",), "wan")):
    for p in (10, 20):
        fig, ax = plt.subplots(figsize=(12, 8))
        for m in MODOS:
            for c in conds:
                desenha(ax, (LAN if c == "lan" else WAN)[(m, p)], m, c, lambda q: q["w"], lambda q: q["tps"] / 1000)
        ax.set_xscale("log")
        ax.xaxis.set_major_locator(FixedLocator(NIVEIS))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        ax.set_xlim(20, 5000)
        ax.set_ylim(0, ymax_v)
        ax.set_xlabel("Clients per channel")
        ax.set_ylabel("Throughput (Ktx/s)")
        legenda(ax, conds, "upper left")
        salva(fig, f"{num}_throughput_{sufixo}_4channels_{p}pct")
        num += 1

# 34–37: latência × vazão
pts_all = todos(("lan", "wan"))
xmax_l = max(q["tps"] for pts in pts_all for q in pts) / 1000 * 1.05
ymin_l = min(q["lat"] for pts in pts_all for q in pts) * 0.85
ymax_l = max(q["lat"] for pts in pts_all for q in pts) * 2.2
for conds, sufixo in ((("lan", "wan"), "lan_x_wan"), (("wan",), "wan")):
    for p in (10, 20):
        fig, ax = plt.subplots(figsize=(12, 8))
        for m in MODOS:
            for c in conds:
                desenha(ax, (LAN if c == "lan" else WAN)[(m, p)], m, c, lambda q: q["tps"] / 1000, lambda q: q["lat"])
        ax.set_yscale("log")
        mais_ticks_y(ax)
        ax.set_xlim(0, xmax_l)
        ax.set_ylim(ymin_l, ymax_l)
        ax.set_xlabel("Throughput (Ktx/s)")
        ax.set_ylabel("Latency (ms)")
        legenda(ax, conds, "upper right")
        salva(fig, f"{num}_latency_{sufixo}_4channels_{p}pct")
        num += 1

# 38: mapa dos pares de destinos; 39: latência das tx cross-channel por par (bateria curta rodar_wan_pares.sh)
CANAIS = {"canal1": "sa-east-1", "canal2": "us-east-1", "canal3": "eu-central-1", "canal4": "ap-northeast-1"}
NOME = {"sa-east-1": "São Paulo", "us-east-1": "N. Virginia", "eu-central-1": "Frankfurt", "ap-northeast-1": "Tokyo"}
LOCAIS = [  # (região da matriz, rótulo, lon, lat, (dx, dy) do rótulo em pontos, alinhamento): o rótulo fica no
    # quadrante por onde nenhum arco passa (12/11/2026: linhas não podem cruzar os nomes)
    ("sa-east-1", "channel 1\nSão Paulo", -46.6, -23.5, (-7, -6), ("right", "top")),
    ("us-east-1", "channel 2\nN. Virginia", -77.5, 39.0, (8, -6), ("left", "top")),
    ("eu-central-1", "channel 3\nFrankfurt", 8.7, 50.1, (9, -6), ("left", "top")),
    ("ap-northeast-1", "channel 4\nTokyo", 139.7, 35.7, (8, -6), ("left", "top")),
]
_linhas = [r for r in csv.reader(open(MATRIZ, encoding="utf-8")) if r]
_idx = {z: i for i, z in enumerate(_linhas[0][1:], 1)}
rtt = lambda a, b: 2 * float(_linhas[_idx[a]][_idx[b]])
# curvatura de cada arco (fração do comprimento; sinal = lado) escolhida para separar os rótulos
CURVA = {("sa-east-1", "us-east-1"): 0.15, ("sa-east-1", "eu-central-1"): -0.32,
         ("us-east-1", "eu-central-1"): 0.12, ("eu-central-1", "ap-northeast-1"): 0.08,
         ("us-east-1", "ap-northeast-1"): 0.16, ("sa-east-1", "ap-northeast-1"): -0.16}
try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
except ImportError:
    ccrs = None
    print("cartopy ausente: mapas (38 e 39b) não gerados")


def mapa(ax, fs_nome=13.5, fs_rtt=13.5, lw=1.0, ms=10):
    """globo inteiro (Robinson, com a borda), centrado em 36°L para os 4 rótulos caberem dentro (conferido pelos cantos);
    nomes sem fundo, no quadrante de cada ponto por onde nenhum arco passa"""
    pc = ccrs.PlateCarree()
    ax.set_global()
    ax.add_feature(cfeature.LAND, facecolor="#e8e6e1", edgecolor="none")
    ax.add_feature(cfeature.OCEAN, facecolor="#f7f9fb")
    ax.coastlines("110m", color="#b9b6ae", lw=0.5)
    for i, (za, _, xa, ya, _, _) in enumerate(LOCAIS):
        for zb, _, xb, yb, _, _ in LOCAIS[i + 1:]:
            k = CURVA[(za, zb)]
            mx, my = (xa + xb) / 2, (ya + yb) / 2
            cx, cy = mx - (yb - ya) * k * 2, my + (xb - xa) * k * 2  # ponto de controle do arco (Bézier quadrático)
            ts = [t / 60 for t in range(61)]
            xs = [(1 - t) ** 2 * xa + 2 * (1 - t) * t * cx + t ** 2 * xb for t in ts]
            ys = [(1 - t) ** 2 * ya + 2 * (1 - t) * t * cy + t ** 2 * yb for t in ts]
            r = rtt(za, zb)
            ax.plot(xs, ys, transform=pc, color="#1f4e79", lw=lw + r / 110, alpha=0.85, solid_capstyle="round")
            ax.text(xs[30], ys[30], f"{r:g} ms", transform=pc, ha="center", va="center", fontsize=fs_rtt, zorder=4,
                    color="#1b2430", bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#1f4e79", lw=0.8))
    for _, rot, x, y, off, (ha, va) in LOCAIS:
        ax.plot(x, y, "o", transform=pc, ms=ms, mfc="#d62728", mec="white", mew=1.5, zorder=5)
        ax.annotate(rot, xy=(x, y), xycoords=pc._as_mpl_transform(ax), xytext=off, textcoords="offset points",
                    ha=ha, va=va, fontsize=fs_nome, fontweight="bold", color="#1b2430", zorder=6, linespacing=1.05)


if ccrs:
    # tamanho de uma coluna do artigo (~3,4 pol.) × 2: as fontes de 13,5 pt viram ~6,5 pt na página
    fig = plt.figure(figsize=(7.2, 3.8))
    axm = plt.axes(projection=ccrs.Robinson(central_longitude=36))
    mapa(axm)
    if os.environ.get("MAPA_TITULO"):  # 18/11/2026: título dentro da figura (antes posto pelo LaTeX), serifa em negrito
        axm.set_title(os.environ["MAPA_TITULO"], family="serif", fontweight="bold", fontsize=24, pad=6)
    salva(fig, "38_channels_map_rtt_pairs")
    if os.environ.get("SO_MAPA") == "1":
        sys.exit(0)

SEC_PAR = re.compile(r"/dev/shm/g110w_(controle|wan)_c01_d2_([a-z]+)4_pares/")
PARES = {}  # (modo, cond) -> {par de regiões: [média S→D de cada run]}
for arq in args["--pares"]:
    sec = None
    for linha in open(arq, encoding="utf-8", errors="replace"):
        if linha.startswith("ARQ_"):
            m = SEC_PAR.search(linha)
            sec = (m.group(2), "lan" if m.group(1) == "controle" else "wan") if m else None
            continue
        m = re.search(r"\bpares=(\S+)", linha)
        if sec and linha.startswith("modo=") and m:
            run = int(re.search(r"\brun=(\d+)", linha).group(1))
            for item in m.group(1).split(","):
                par, avg = item.split(":")[:2]
                if avg != "NA":
                    k = tuple(CANAIS[c] for c in sorted(par.split("-")))  # na ordem dos canais (1 = São Paulo, ...)
                    PARES.setdefault(sec, {}).setdefault(k, {})[run] = float(avg)


def grafico_pares(ax):
    pares = sorted({k for d in PARES.values() for k in d}, key=lambda k: rtt(*k))
    xs = range(len(pares))
    for m in MODOS:
        for c in ("lan", "wan"):
            d = PARES.get((m, c), {})
            if d:
                desenha(ax, [dict(i=i, lat=st.median(d[k].values())) for i, k in enumerate(pares) if k in d], m, c,
                        lambda q: q["i"], lambda q: q["lat"])
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{NOME[a]}–\n{NOME[b]}\n(RTT {rtt(a, b):g} ms)" for a, b in pares], fontsize=11.5)
    ax.set_xlim(-0.4, len(pares) - 0.6)
    ax.set_yscale("log")
    mais_ticks_y(ax)
    lats = [v for d in PARES.values() for runs in d.values() for v in runs.values()]
    ax.set_ylim(min(lats) * 0.8, max(lats) * 2.2)  # folga em cima para a legenda
    ax.set_ylabel("Cross-channel latency (ms)")
    ax.set_xlabel("Destination pair (ordered by RTT)")
    legenda(ax, ("lan", "wan"), "upper left")


if PARES:
    fig, ax = plt.subplots(figsize=(13, 8))
    grafico_pares(ax)
    salva(fig, "39_latency_per_destination_pair_4channels_10pct")
    if ccrs:
        fig = plt.figure(figsize=(13, 12))
        mapa(fig.add_axes([0.03, 0.53, 0.94, 0.45], projection=ccrs.Robinson(central_longitude=36)), 15.5, 15, 1.5, 12)
        grafico_pares(fig.add_axes([0.08, 0.07, 0.88, 0.40]))
        salva(fig, "39b_map_and_latency_per_destination_pair_4channels_10pct")

print(PASTA)
print("\n".join(feitos))
