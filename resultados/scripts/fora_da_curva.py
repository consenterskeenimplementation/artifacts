#!/usr/bin/env python3
"""
Lista os pontos fora da curva de uma bateria (para refazer SÓ esses runs). Lê o resultado.txt; quando um (nível, run)
foi refeito, vale a linha mais recente. Critérios (vazão e latência na marca D, entre as linhas 24 e 25 do Alg. 1):
  run fora da curva   : vazão a mais de LIM_TPS da mediana do nível, ou latência S→D (p50) a mais de LIM_LAT da
                        mediana do nível
  nível fora da curva : mediana da vazão do nível abaixo de ambos os vizinhos (vale) ou acima de ambos (pico) por
                        mais de LIM_TPS — refaz os runs do nível
Imprime "w:run" por linha. Uso: python3 fora_da_curva.py <resultado.txt> [--explica]
"""
import re
import statistics as st
import sys

LIM_TPS, LIM_LAT = 0.10, 0.30
PAT = re.compile(r"workers_por_shard=(\d+) run=(\d+) .*?d_tps=(\d+) .*?sd_p50=([\d.]+|NA)ms")
runs = {}
for linha in open(sys.argv[1], encoding="utf-8", errors="replace"):
    m = PAT.search(linha)
    if m and m.group(4) != "NA":
        runs[(int(m.group(1)), int(m.group(2)))] = (float(m.group(3)), float(m.group(4)))  # a última vence

niveis = sorted({w for w, _ in runs})
med = {w: (st.median(v[0] for k, v in runs.items() if k[0] == w), st.median(v[1] for k, v in runs.items() if k[0] == w))
       for w in niveis}
refazer, motivo = set(), {}
for (w, r), (tps, lat) in runs.items():
    mt, ml = med[w]
    if mt > 0 and abs(tps - mt) / mt > LIM_TPS:
        refazer.add((w, r)); motivo[(w, r)] = f"vazão {tps:.0f} vs mediana {mt:.0f}"
    elif ml > 0 and abs(lat - ml) / ml > LIM_LAT:
        refazer.add((w, r)); motivo[(w, r)] = f"latência {lat:.1f} vs mediana {ml:.1f} ms"
for i in range(1, len(niveis) - 1):
    a, w, b = niveis[i - 1], niveis[i], niveis[i + 1]
    t, ta, tb = med[w][0], med[a][0], med[b][0]
    if t < min(ta, tb) * (1 - LIM_TPS) or t > max(ta, tb) * (1 + LIM_TPS):
        for k in runs:
            if k[0] == w:
                refazer.add(k); motivo.setdefault(k, f"nível {w} fora da curva ({t:.0f} entre {ta:.0f} e {tb:.0f})")
for w, r in sorted(refazer):
    print(f"{w}:{r}" + (f"  # {motivo[(w, r)]}" if "--explica" in sys.argv else ""))
