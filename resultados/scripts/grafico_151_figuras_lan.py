#!/usr/bin/env python3
"""
Gráfico 151 — reedição em inglês dos gráficos (14/10/2026), com a rodada de correções de
15/10/2026 (segunda leva, "MAIS CORREÇÕES"). Mesmos dados dos gráficos 148/149 (mesmas fontes --sem/--cross/--b5).

Mudanças desta rodada (além das já feitas na rodada anterior: texto em inglês, sem nota de rodapé/caixa de
joelho/círculo de joelho em 01-15, legenda sempre dentro da área, sem "(1 shard)"/"(N núcleos)", Skeen-puro ->
Skeen, SmartBFT nativo -> BFT-SMaRt, fusão 6/5 núcleos do Skeen-BFT, sem linha pontilhada de referência,
recorte do colapso por sobrecarga em 01-15):
  - Sem título embutido em nenhum gráfico (o título fica só na legenda da figura no artigo).
  - "shard"/"chain" trocado por "channel" em todo texto desenhado (eixos, legendas): inclui "cross-chain" ->
    "cross-channel", "Number of shards" -> "Number of channels", "efficiency per shard" -> "per channel".
  - Unidade de vazão "Kops/sec" -> "ktx/s" em todos os gráficos.
  - Sem rótulo numérico nas curvas (removidos de 16, 17, 18-21; a função rotulo() foi removida).
  - "Raft native" -> "Raft" (tirado o "native").
  - Gráficos 22/23 (latência sob carga leve x % cross-channel) removidos — não são mais gerados.
  - Gráficos 04-06, 07-09, 10-12, 13-15 (cada trio por % de cross-channel, mesma config): eixos x/y com a
    MESMA escala dentro do trio, para comparação direta entre os 3 níveis de %. Por padrão (pasta principal)
    sai com escala compartilhada; a pasta comparacao_escala_livre/ traz a mesma figura com escala automática
    (antiga), só para comparar as duas formas.
  - Novos gráficos 24-29: para 4 e para 6 shards, um gráfico combinando as curvas de 2 E 3 destinos no mesmo
    painel (6 curvas: 3 protocolos x 2 contagens de destino), um por % (5/10/20). Mesma regra de escala
    compartilhada do item anterior. Ainda não referenciados no artigo.
  - Gráficos 18-21 (vazão/custo x % cross-channel): Skeen-BFT com 2 e 3 destinos agora usa cor E marcador
    diferentes (não só traço sólido/tracejado), porque as curvas estavam parecidas demais depois de tirar os
    rótulos numéricos que ajudavam a diferenciar.
  - Eixo Y de latência (log, gráficos 01-15 e 24-29): mais marcações (1-2-5 por década, maior e menor) para
    ler valores intermediários.
  - Cada gráfico agora também sai em .pdf (vetorial), além do .png de sempre, para o artigo não perder
    qualidade ao dar zoom. O .png continua sendo o formato do pacote
    WhatsApp/deck; o .pdf é o que vai para as pastas figures/ dos artigos.
Uso (da raiz): python3 resultados/scripts/grafico_151_figuras_lan.py --sem <seqsemcross_*> --cross <seqcross46_*> --b5 <seqbft5_*>
"""
import os
import re
import statistics as st
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, LogLocator

PASTA = os.environ.get("SAIDA", f"resultados/graficos/figuras_lan_{time.strftime('%Y%m%d_%H%M%S')}")
BASE = re.compile(r"modo=(\w+) cross=[\d.]+ workers_por_shard=(\d+) run=(\d+) .*?d_tps=(\d+) d_n=(\d+) incompletas_d=(\d+) "
                  r"sd_avg=([\d.]+|NA)ms")
SEC = re.compile(r"/dev/shm/g109dx_c(\d+)_d(\d)_([a-z]+)(\d)_([a-z0-9]+)/")
EST = {"puro": ("Skeen", "#2ca02c", "^", "-"), "cft": ("Skeen-CFT", "#1f77b4", "o", "-"),
       "raft": ("Raft", "#d62728", "s", "--"), "bftM": ("Skeen-BFT", "#9467bd", "D", "-"),
       "bftsmartM": ("BFT-SMaRt", "#ff7f0e", "*", "--")}
# cor/marcador específicos por nº de destinos, só onde a curva padrão (mesma cor, só muda o traço) fica
# parecida demais entre 2 e 3 destinos nos gráficos 18-21/24-29: 3 destinos usa um
# tom mais escuro da mesma cor e um marcador diferente do de 2 destinos, em vez de só o traço tracejado.
DEST_COR = {"puro": {2: "#2ca02c", 3: "#14591f"}, "cft": {2: "#1f77b4", 3: "#0b3d66"},
            "bftM": {2: "#9467bd", 3: "#5e3c99"}}
DEST_MK = {"puro": {2: "^", 3: "v"}, "cft": {2: "o", 3: "s"}, "bftM": {2: "D", 3: "P"}}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 14, "axes.grid": True, "grid.alpha": 0.3, "legend.fontsize": 11.5, "figure.dpi": 150})
# FONTE_ARTIGO=1 (pedido de 18/11/2026: "aumentar as fontes"): no artigo cada painel ocupa ~0,325 da largura do texto
# (~2,3 pol.); desenhado em 12x8 pol. ele encolhia para menos de 1/5 e o texto ficava com ~3 pt. Aqui o painel é desenhado
# em 6,4x4,6 pol. com fontes maiores, o que dá ~6–7 pt depois da redução.
FONTE_ARTIGO = os.environ.get("FONTE_ARTIGO") == "1"
TAM_FIG = (6.4, 4.6) if FONTE_ARTIGO else (12, 8)
LW_MS = (2.0, 5.5) if FONTE_ARTIGO else (2.4, 7)
if FONTE_ARTIGO:
    plt.rcParams.update({"font.size": 17, "axes.labelsize": 18, "xtick.labelsize": 15.5, "ytick.labelsize": 15.5,
                         "legend.fontsize": 12.5, "legend.handlelength": 1.8, "legend.borderpad": 0.3,
                         "legend.labelspacing": 0.25, "legend.handletextpad": 0.5})

grupos, atual = {"--sem": [], "--cross": [], "--b5": []}, None
for a in sys.argv[1:]:
    if a in grupos:
        atual = a
    elif atual:
        grupos[atual].append(a)


def pct(c):
    return int(round(float(c[0] + "." + c[1:]) * 100))


def ler(arqs, aceita):
    """devolve {(modo, shards, pct, dest, sufixo): {(w, run): r}}"""
    out = {}
    for arq in arqs:
        sec = None
        for linha in open(arq, encoding="utf-8", errors="replace"):
            if linha.startswith("ARQ_"):
                m = SEC.search(linha)
                sec = None
                if m and m.group(5) in aceita:
                    p = pct(m.group(1))
                    sec = (m.group(3), int(m.group(4)), p, 0 if p == 0 else int(m.group(2)), m.group(5))
                continue
            m = BASE.search(linha)
            if sec and m and m.group(1) == sec[0] and m.group(7) != "NA":
                dn, inc = int(m.group(5)), int(m.group(6))
                out.setdefault(sec, {})[(int(m.group(2)), int(m.group(3)))] = dict(
                    w=int(m.group(2)), tps=float(m.group(4)), lat=float(m.group(7)), inc=inc / max(1, dn + inc))
    return out


def junta(d, base_sufs, ext_suf):
    """curva base (primeiro sufixo de base_sufs que existir) + níveis novos da extensão"""
    out = {}
    chaves = {k[:4] for k in d}
    for c in chaves:
        base = next((d[c + (s,)] for s in base_sufs if c + (s,) in d), None)
        if base is None:
            continue
        cur = dict(base)
        ws = {w for w, _ in cur}
        for kk, r in d.get(c + (ext_suf,), {}).items():
            if kk[0] not in ws:
                cur[kk] = r
        out[c] = cur
    return out


def funde_nucleos(d, base, alt, so_alt_em=frozenset()):
    """cria modo base+'M': prefere os dados de 'base' (6 núcleos); usa 'alt' (5 núcleos) só onde 'base' não
    existe para aquele nº de shards (hoje: shards em so_alt_em, ou qualquer config sem dado nenhum em 'base')"""
    chaves = {k[1:] for k in d if k[0] in (base, alt)}
    for c in chaves:
        usa_alt = c[0] in so_alt_em or (base,) + c not in d
        origem = alt if usa_alt else base
        if (origem,) + c in d:
            d[(base + "M",) + c] = d[(origem,) + c]


sem = ler(grupos["--sem"], {"s", "s2", "s3"})
for k in [k for k in sem if k[4] == "s" and k[0] in ("bft", "bftsmart")]:
    del sem[k]  # seguidoras com 4 núcleos: descartado (refeito em _s2)
SEM = junta(sem, ("s2", "s"), "s3")
CROSS = junta(ler(grupos["--cross"], {"p", "p3"}), ("p",), "p3")
for k, v in junta(ler(grupos["--b5"], {"b5"}), ("b5",), "-").items():
    (SEM if k[2] == 0 else CROSS)[(k[0] + "5",) + k[1:]] = v
    if k[2] == 0 and k[1] > 1:
        CROSS[(k[0] + "5",) + k[1:]] = v  # linha de base sem cross do BFT 5 núcleos (4 e 6 shards)

for d in (SEM, CROSS):
    funde_nucleos(d, "bft", "bft5", so_alt_em={6})  # com 6 shards, BFT 6 núcleos não coube na máquina
    funde_nucleos(d, "bftsmart", "bftsmart5")


def curva(d, ch):
    rs = d.get(ch, {})
    return [dict(w=w, tps=st.median(r["tps"] for r in g), lat=st.median(r["lat"] for r in g), inc=st.median(r["inc"] for r in g))
            for w in sorted({r["w"] for r in rs.values()}) for g in [[r for r in rs.values() if r["w"] == w]]]


def recorta(pts, lim=0.90):
    """pára de desenhar no primeiro nível cujo tps cai mais de (1-lim) abaixo do máximo corrente da curva
    (colapso por sobrecarga) — não afeta capacidade/joelho, só a linha desenhada nos gráficos 01-15/24-29"""
    out, maxtps = [], 0.0
    for p in pts:
        if maxtps and p["tps"] < maxtps * lim:
            break
        maxtps = max(maxtps, p["tps"])
        out.append(p)
    return out


def joelho(pts):
    ok = [p for p in pts if p["inc"] <= 0.01]
    if not ok:
        return None, None
    cap = max(ok, key=lambda p: p["tps"])
    return next(p for p in ok if p["tps"] >= 0.9 * cap["tps"]), cap


def cap(d, ch):
    j, c = joelho(curva(d, ch))
    return (c["tps"] / 1000, j) if c else (None, None)


os.makedirs(PASTA, exist_ok=True)
feitos = []


def salva_fig(fig, caminho_png):
    """salva em .png (WhatsApp/visualização rápida) e também em .pdf (vetorial, sem perda de qualidade ao dar
    zoom — usado nas figuras do artigo) a partir do mesmo caminho"""
    fig.savefig(caminho_png, bbox_inches="tight")
    fig.savefig(caminho_png[:-4] + ".pdf", bbox_inches="tight")


def estilo(m, dd=None):
    rot, cor, mk, ls = EST[m]
    if dd is not None:
        cor = DEST_COR.get(m, {}).get(dd, cor)
        mk = DEST_MK.get(m, {}).get(dd, mk)
        ls = "-" if dd == 2 else "--"
        rot = f"{rot}, {dd} destinations"
    return rot, cor, mk, ls


def mais_ticks_y(ax):
    """mais marcações no eixo Y log (1-2-5 por década, maior e menor) para ler melhor valores intermediários"""
    if FONTE_ARTIGO:  # 18/11/2026: rótulos 10^n só nas décadas (coluna do eixo bem mais estreita); 2..9 como marcas menores
        from matplotlib.ticker import LogFormatterSciNotation, NullFormatter
        ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0,)))
        ax.yaxis.set_minor_locator(LogLocator(base=10, subs=tuple(range(2, 10))))
        ax.yaxis.set_major_formatter(LogFormatterSciNotation(base=10))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.grid(True, which="minor", axis="y", alpha=0.15)
        return
    ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    ax.yaxis.set_minor_locator(LogLocator(base=10, subs=tuple(range(2, 10))))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:g}"))
    ax.grid(True, which="minor", axis="y", alpha=0.15)


def limites_grupo(series_list):
    """série list: lista de listas de (chave, pts) de cada painel do grupo; devolve (xlim, ylim) comuns,
    já com a mesma margem usada no desenho individual (None se não houver nenhum ponto)"""
    xs, ys = [], []
    for series in series_list:
        for _, pts in series:
            pts = recorta(pts)
            xs += [q["tps"] / 1000 for q in pts]
            ys += [q["lat"] for q in pts]
    if not xs:
        return None, None
    return (0, max(xs) * 1.05), (min(ys) * 0.85, max(ys) * 2.2)


# ROTULO_CANAIS=1: escreve no canto inferior direito de cada painel 01-03 o número de
# canais ("1 channel", "4 channels", "6 channels"), para identificar o painel quando as três figuras ficam lado a lado
# no artigo. SO_NOCROSS=1 para depois das 01-03.
ROTULO_CANAIS = os.environ.get("ROTULO_CANAIS") == "1"
# ROTULO_PROF=1 (18/11/2026): rótulos como foram postos no LaTeX: "1 channel"... nas 01-03 e "5%"/"10%"/"20%" nas 24-29
ROTULO_PROF = os.environ.get("ROTULO_PROF") == "1"


def poe_rotulo_livre(fig, ax, leg, texto):
    """rótulo do painel sempre no canto inferior direito (18/11/2026); se ele encostar numa curva, o limite inferior do
    eixo y (log) desce aos poucos até abrir espaço livre embaixo das curvas"""
    import numpy as np
    tam = 21 if "channel" in texto else 24
    t = ax.text(0.97, 0.03, texto, transform=ax.transAxes, ha="right", va="bottom", fontsize=tam, fontweight="bold",
                family="serif", bbox=dict(boxstyle="square,pad=0.15", facecolor="white", edgecolor="none", alpha=0.85))
    for _ in range(12):
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        pts = []
        for ln in ax.get_lines():
            xy = ax.transData.transform(ln.get_xydata())
            for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
                u = np.linspace(0, 1, 40)
                pts.append(np.column_stack([x0 + (x1 - x0) * u, y0 + (y1 - y0) * u]))
        pts = np.vstack(pts)
        bb = t.get_window_extent(r).expanded(1.08, 1.5)
        dentro = int(((pts[:, 0] >= bb.x0) & (pts[:, 0] <= bb.x1) & (pts[:, 1] >= bb.y0) & (pts[:, 1] <= bb.y1)).sum())
        if os.environ.get("DEBUG_ROTULO"):
            print("ROTULO", texto, ax.get_ylim()[0], dentro)
        if dentro == 0:
            if os.environ.get("ROTULO_INVISIVEL") == "1":  # 18/11/2026: mesmo enquadramento, texto posto pelo LaTeX (tikz)
                t.set_visible(False)
            return
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo / 1.35, hi)


def figura(nome, series, xlim=None, ylim=None, pasta=None, rotulo=None, rotulo_legenda=None):
    pasta = pasta or PASTA
    fig, ax = plt.subplots(figsize=TAM_FIG)
    for key, pts in series:
        m, dd = key if isinstance(key, tuple) else (key, None)
        rot, cor, mk, ls = estilo(m, dd)
        pts = recorta(pts)
        if not pts:
            continue
        ax.plot([q["tps"] / 1000 for q in pts], [q["lat"] for q in pts], color=cor, marker=mk, ls=ls, lw=LW_MS[0], ms=LW_MS[1], label=rot)
    ax.set_yscale("log")
    mais_ticks_y(ax)
    if ylim:
        ax.set_ylim(*ylim)
    else:
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi * 2.2)
    if xlim:
        ax.set_xlim(*xlim)
    ax.set_xlabel("Throughput (ktx/s)")
    ax.set_ylabel("Latency (ms)")
    if rotulo_legenda:  # legenda com título fica alta: fixa no canto superior direito, 2 colunas, e mais folga no topo
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi * 2.5)
        leg = ax.legend(loc="upper right", ncol=2, frameon=True, framealpha=0.92, title=rotulo_legenda, title_fontsize=15)
    elif FONTE_ARTIGO and len(series) > 5:  # 6 curvas (2 e 3 destinos): legenda em 2 colunas no topo, com folga acima dos dados
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi * 3)
        leg = ax.legend(loc="upper center", ncol=2, frameon=True, framealpha=0.92, fontsize=10.8, columnspacing=0.6,
                        handlelength=1.5)
    else:
        leg = ax.legend(loc="best", frameon=True, framealpha=0.92)
    if rotulo_legenda:  # identificação do painel como título da legenda (24-29: o canto livre é o da legenda)
        leg.get_title().set_fontweight("bold")
    if rotulo and FONTE_ARTIGO:  # estilo do rótulo usado no LaTeX: serifa, negrito
        poe_rotulo_livre(fig, ax, leg, rotulo)
    elif rotulo:
        ax.text(0.97, 0.04, rotulo, transform=ax.transAxes, ha="right", va="bottom", fontsize=22, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="#555555", alpha=0.95))
    salva_fig(fig, f"{pasta}/{nome}")
    plt.close(fig)
    if pasta == PASTA:
        feitos.append(nome)


def grupo_escala_comum(config, rotulos=None, cantos=None):
    """config: lista de (nome, series) do grupo; desenha com escala compartilhada na pasta principal e, à
    parte, com escala livre (automática) em comparacao_escala_livre/, só para comparar as duas formas.
    rotulos: texto do canto de cada painel (ROTULO_CANAIS=1), só na pasta principal"""
    xlim, ylim = limites_grupo([s for _, s in config])
    for i, (nome, series) in enumerate(config):
        figura(nome, series, xlim=xlim, ylim=ylim, rotulo_legenda=rotulos[i] if rotulos else None,
               rotulo=cantos[i] if cantos else None)
    sub = f"{PASTA}/comparacao_escala_livre"
    os.makedirs(sub, exist_ok=True)
    for nome, series in config:
        figura(nome, series, pasta=sub)


# 01-03: no cross-channel, 1 / 4 / 6 shards (configs com vazão muito diferente entre si — sem escala comum)
num = 1
for n in (1, 4, 6):
    modos = ["puro", "cft", "raft", "bftM", "bftsmartM"]
    series = [(m, curva(SEM, (m, 1 if m in ("raft", "bftsmartM") else n, 0, 0))) for m in modos]
    figura(f"{num:02d}_no_cross_{n}shard{'s' if n > 1 else ''}.png", series,
           rotulo=(f"{n} channel{'s' if n > 1 else ''}" if ROTULO_CANAIS or ROTULO_PROF else None))
    num += 1
if os.environ.get("SO_NOCROSS") == "1" or os.environ.get("SO_2N3") == "1":
    if os.environ.get("SO_2N3") == "1":  # só as 24-29 (2 e 3 destinos no mesmo painel), pedido de 18/11/2026
        num = 24
        for n in (4, 6):
            config = []
            for p in (5, 10, 20):
                series = [((m, dd), curva(CROSS, (m, n, p, dd))) for m in ("puro", "cft", "bftM") for dd in (2, 3)]
                config.append((f"{num:02d}_{n}shards_2n3destinations_{p:02d}pct.png", series))
                num += 1
            grupo_escala_comum(config, [f"{n} channels, {p}% cross-channel" for p in (5, 10, 20)] if ROTULO_CANAIS else None,
                               [f"{p}%" for p in (5, 10, 20)] if ROTULO_PROF else None)
    print("\n".join(feitos))
    print(PASTA)
    sys.exit(0)

# 04-15: 4/6 shards x 2/3 destinations x 5/10/20% cross-channel — escala comum dentro de cada trio de %
for n, dd in ((4, 2), (4, 3), (6, 2), (6, 3)):
    config = []
    for p in (5, 10, 20):
        series = [(m, curva(CROSS, (m, n, p, dd))) for m in ("puro", "cft", "bftM")]
        config.append((f"{num:02d}_{n}shards_{dd}destinations_{p:02d}pct.png", series))
        num += 1
    grupo_escala_comum(config)

# 16 escalabilidade (sem título, sem rótulo numérico, ktx/s, eixo x = número de canais, Raft sem "native")
fig, ax = plt.subplots(figsize=(12, 8))
caps1 = {}
PROTS = ("puro", "cft", "bftM")
for m in PROTS:
    rot, cor, mk, ls = EST[m]
    xs, ys = [], []
    for n in (1, 4, 6):
        c, _ = cap(SEM, (m, n, 0, 0))
        if c:
            xs.append(n); ys.append(c)
    if not xs:
        continue
    caps1[m] = dict(zip(xs, ys))
    ax.plot(xs, ys, color=cor, marker=mk, ls=ls, lw=3, ms=11, label=rot)
for m in ("raft", "bftsmartM"):
    c, _ = cap(SEM, (m, 1, 0, 0))
    if c:
        ax.scatter([1], [c], color=EST[m][1], marker=EST[m][2], s=220, zorder=6, label=EST[m][0], edgecolors="white")
ax.set_xticks([1, 4, 6]); ax.set_xlim(0.5, 6.8); ax.set_ylim(0, 100)
ax.set_xlabel("Number of channels"); ax.set_ylabel("Throughput (ktx/s)")
ax.legend(loc="upper left", frameon=True, framealpha=0.92)
salva_fig(fig, f"{PASTA}/16_scalability_no_cross.png")
plt.close(fig)
feitos.append("16_scalability_no_cross.png")

# 17 eficiência por canal (sem título, sem rótulo numérico)
fig, ax = plt.subplots(figsize=(12, 8))
for m in PROTS:
    v = caps1.get(m, {})
    if 1 not in v:
        continue
    rot, cor, mk, ls = EST[m]
    xs = [n for n in (1, 4, 6) if n in v]
    ys = [100 * v[n] / (n * v[1]) for n in xs]
    ax.plot(xs, ys, color=cor, marker=mk, ls=ls, lw=3, ms=11, label=rot)
ax.set_xticks([1, 4, 6]); ax.set_xlim(0.5, 6.8); ax.set_ylim(0, 115)
ax.set_xlabel("Number of channels"); ax.set_ylabel("Efficiency per channel (%)")
ax.legend(loc="lower left", frameon=True, framealpha=0.92)
salva_fig(fig, f"{PASTA}/17_efficiency_per_shard_no_cross.png")
plt.close(fig)
feitos.append("17_efficiency_per_shard_no_cross.png")

# 18-21: capacidade/custo x % cross-channel (sem título, sem rótulo numérico; Skeen-BFT 2 vs 3 destinos com
# cor e marcador diferentes, não só traço, porque ficavam parecidas demais sem os rótulos numéricos)
num = 18
for tipo in ("cap", "rel"):
    for n in (4, 6):
        fig, ax = plt.subplots(figsize=(12, 8))
        for m in PROTS:
            b, _ = cap(CROSS, (m, n, 0, 0))
            for dd in (2, 3):
                rot, cor, mk, ls = estilo(m, dd)
                xs, ys = [], []
                for p in (0, 5, 10, 20):
                    c, _ = cap(CROSS, (m, n, p, 0 if p == 0 else dd))
                    if c is None or (tipo == "rel" and not b):
                        continue
                    xs.append(p); ys.append(c if tipo == "cap" else 100 * c / b)
                if len(xs) > 1:
                    ax.plot(xs, ys, color=cor, marker=mk, ls=ls, lw=2.8, ms=10, label=rot)
        ax.set_xticks([0, 5, 10, 20]); ax.set_xlim(-1, 25); ax.set_ylim(bottom=0)
        ax.set_xlabel("% of cross-channel transactions")
        if tipo == "cap":
            ax.set_ylabel("Throughput (ktx/s)")
            nome = f"{num}_throughput_x_pct_crosschain_{n}shards.png"
        else:
            ax.set_ylabel("Capacity (% of no cross-channel)")
            ax.set_ylim(0, 110)
            nome = f"{num}_cost_of_crosschain_{n}shards.png"
        ax.legend(loc="lower left", ncol=2, frameon=True, framealpha=0.92)
        salva_fig(fig, f"{PASTA}/{nome}")
        plt.close(fig)
        feitos.append(nome)
        num += 1

# 24-29: gráfico combinado com 6 curvas (3 protocolos x 2/3 destinos juntos no mesmo painel), 4 e 6 shards,
# um por % de cross-channel — 15/10/2026 para comparar 2 e 3 destinos lado a lado;
# ainda não referenciado no artigo.
num = 24
for n in (4, 6):
    config = []
    for p in (5, 10, 20):
        series = [((m, dd), curva(CROSS, (m, n, p, dd))) for m in PROTS for dd in (2, 3)]
        config.append((f"{num:02d}_{n}shards_2n3destinations_{p:02d}pct.png", series))
        num += 1
    grupo_escala_comum(config)

print("\n".join(feitos))
print(PASTA)
