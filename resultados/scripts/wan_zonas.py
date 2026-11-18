#!/usr/bin/env python3
"""Arquivos de zona do teste de WAN (um canal por local, local s = IP 127.0.s.1 = linha s da matriz de regiões).
Uso: wan_zonas.py <matriz.csv> <N canais> <saída ips.csv> <saída lats.csv> [--controle]
  --controle: mesma topologia, 1 ms em cada sentido entre todos os locais (RTT 2 ms, como a LAN das baterias)."""
import csv
import sys

matriz, n, ips, lats = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
controle = "--controle" in sys.argv[5:]
linhas = [r for r in csv.reader(open(matriz, encoding="utf-8")) if r]
zonas = linhas[0][1:n + 1]
if len(zonas) < n or len(linhas) < n + 1:
    sys.exit(f"matriz {matriz} tem menos de {n} regiões")
with open(ips, "w") as f:
    f.write("Zone,IP\n" + "".join(f"{z},127.0.{s}.1\n" for s, z in enumerate(zonas, 1)))
with open(lats, "w") as f:
    f.write(",".join(["zona"] + zonas) + "\n")
    for i, z in enumerate(zonas, 1):
        assert linhas[i][0] == z, f"linha {i} da matriz é {linhas[i][0]}, esperado {z}"
        f.write(",".join([z] + ["0" if i == j else ("1" if controle else linhas[i][j]) for j in range(1, n + 1)]) + "\n")
