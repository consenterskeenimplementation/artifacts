#!/usr/bin/env python3
"""Latência × vazão do Skeen entre plataformas (Fabric × CometBFT), varredura em carga controlada (varredura_hetero.sh):
mediana de 3 execuções por nível (pontos individuais visíveis); globais = envio no cliente → entrega no último destino.
Uso (da raiz): python3 cometbft/grafico_varredura_hetero.py cometbft/resultados/varredura_hetero_<data>.txt"""
import collections, re, statistics as st, sys, time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

runs = collections.defaultdict(list)
for l in open(sys.argv[1]):
    m = re.search(r"nivel=(\d+) run=\d+ d_tps=(\d+) .*x_sd_p50=([\d.]+)ms x_sd_p99=([\d.]+)ms .*i_sd_p50=([\d.]+)ms .*VEREDITO (\w+)", l)
    if m:
        runs[int(m[1])].append(dict(tps=int(m[2]), g50=float(m[3]), g99=float(m[4]), l50=float(m[5]), ok=m[6] == "OK"))
niveis = sorted(runs)
med = lambda n, k: st.median(r[k] for r in runs[n])
print("nivel  vazao  globais_p50  globais_p99  locais_p50  veredito")
for n in niveis:
    print(f"{n:5d} {med(n,'tps'):6.0f} {med(n,'g50'):10.0f} {med(n,'g99'):11.0f} {med(n,'l50'):10.0f}   {sum(r['ok'] for r in runs[n])}/{len(runs[n])} OK")
plt.rcParams.update({"font.size": 13, "axes.grid": True, "grid.alpha": 0.3})
fig, ax = plt.subplots(figsize=(7.5, 4.8))
for k, rot, cor, mk in (("g50", "Global (Fabric + CometBFT), p50", "#9467bd", "D"), ("l50", "Local, p50", "#1f77b4", "o"),
                        ("g99", "Global, p99", "#9467bd", "")):
    xs = [med(n, "tps") for n in niveis]; ys = [med(n, k) for n in niveis]
    ax.plot(xs, ys, color=cor, marker=mk or None, ls=":" if k == "g99" else "-", lw=2, label=rot)
    if k != "g99":
        ax.scatter([r["tps"] for n in niveis for r in runs[n]], [r[k] for n in niveis for r in runs[n]], color=cor, s=14, alpha=0.35)
ax.set_xlabel("Throughput (tx/s, delivered)"); ax.set_ylabel("Latency (ms)"); ax.set_ylim(0, None)
ax.legend(loc="upper left", frameon=True)
saida = f"resultados/graficos/hetero_fabric_cometbft_carga_{time.strftime('%Y%m%d_%H%M')}.png"
fig.tight_layout(); fig.savefig(saida, dpi=150); fig.savefig(saida[:-4] + ".pdf"); print(saida)
