#!/usr/bin/env python3
"""Quantis da CDF de latência (S→D) a partir das amostras gravadas pelo txtiming_agrega_d.py (TXTIMING_AMOSTRAS_DIR),
juntando os runs de cada configuração (tag = nome do arquivo sem o _<t0>.csv). Uma linha por (tag, tipo):
  CDF tag=<tag> tipo=todas|globais n=<amostras> q=<lat no quantil 0,5%>,<1%>,...,<99,5%>,<99,9%>,<máx>
tipo=todas: todas as tx; tipo=globais: só as cross-channel (2+ destinos).
--descarta-travados (12/11/2026): descarta, em cada configuração, o run cujo nº de amostras de tx globais ficou abaixo de
70% do maior run dela (run em que as tx globais travaram) e lista os descartados numa linha CDF_DESCARTE.
--por-par (12/11/2026): também uma curva das tx globais de cada conjunto de destinos dentro de cada configuração
(tag=<tag>_par<a><b>, com canal<a> e canal<b>); na configuração global (par sorteado), dá a latência de cada par sob a
mesma carga, e a curva da configuração é exatamente a mistura das curvas dos pares.
Uso: cdf_quantis.py <dir das amostras> [--descarta-travados] [--por-par]"""
import collections
import glob
import os
import re
import statistics
import sys

QS = [i / 200 for i in range(1, 200)] + [0.999, 1.0]
runs = collections.defaultdict(list)  # tag -> [(arquivo, todas, globais)]
for arq in sorted(glob.glob(os.path.join(sys.argv[1], "*.csv"))):
    tag = re.sub(r"_\d+\.csv$", "", os.path.basename(arq))
    todas, globais, pares = [], [], collections.defaultdict(list)
    with open(arq) as f:
        next(f, None)
        for linha in f:
            tipo, dest, v = linha.rstrip("\n").split(",")
            todas.append(float(v))
            if tipo == "x":
                globais.append(float(v))
                pares["".join(re.findall(r"\d+", dest))].append(float(v))
    runs[tag].append((os.path.basename(arq), todas, globais, pares))
lat = collections.defaultdict(lambda: {"todas": [], "globais": []})
for tag, rs in runs.items():
    med = max(len(g) for _, _, g, _ in rs)
    for arq, todas, globais, pares in rs:
        if "--descarta-travados" in sys.argv and len(globais) < 0.7 * med:
            print(f"CDF_DESCARTE tag={tag} arquivo={arq} globais={len(globais)} maior_run={med:.0f}")
            continue
        lat[tag]["todas"] += todas
        lat[tag]["globais"] += globais
        if "--por-par" in sys.argv:
            for par, v in pares.items():
                lat[f"{tag}_par{par}"]["globais"] += v
for tag in sorted(lat):
    for tipo, v in lat[tag].items():
        if v:
            v.sort()
            q = ",".join(f"{v[min(len(v) - 1, int(p * len(v)))]:.1f}" for p in QS)
            print(f"CDF tag={tag} tipo={tipo} n={len(v)} q={q}")
