#!/usr/bin/env python3
"""Confere, numa foto `ss -tnpH state established`, que toda conexão de um local para outro sai pelo IP do local de
origem (127.0.a.1). Uma conexão entre locais saindo por 127.0.0.1 escaparia da latência de WAN (o filtro é por origem).
O local de cada processo vem dos arquivos de pid do teste: s<S>r<R>.pid (réplica do canal S) e c<S>.pid (cliente do
canal S). Uso: wan_confere_conexoes.py <conexoes.txt> <dir com os .pid>. Sai com código 1 se houver conexão errada."""
import glob
import os
import re
import sys

foto, d = sys.argv[1], sys.argv[2]
local_do_pid = {}
for f in glob.glob(os.path.join(d, "*.pid")):
    m = re.match(r"(?:s(\d+)r\d+|c(\d+))\.pid$", os.path.basename(f))
    if m:
        local_do_pid[int(open(f).read().strip())] = int(m.group(1) or m.group(2))

ok, ruins, sem_dono = 0, [], 0
for linha in open(foto):
    campos = linha.split()
    if len(campos) < 4:
        continue
    loc, par = campos[2], campos[3]
    mp = re.match(r"127\.0\.(\d+)\.1:(\d+)$", par)
    if not mp or mp.group(1) == "0":
        continue  # só conexões para o IP de um local (ignora 127.0.0.1: admin, operações)
    destino = int(mp.group(1))
    pids = [int(p) for p in re.findall(r"pid=(\d+)", linha)]
    origem = next((local_do_pid[p] for p in pids if p in local_do_pid), None)
    if origem is None:
        sem_dono += 1  # lado servidor da conexão (aceita pelo listener) ou processo que não é do teste
        continue
    if origem == destino:
        continue
    if loc.startswith(f"127.0.{origem}.1:"):
        ok += 1
    else:
        ruins.append(f"canal{origem} -> {par} saindo por {loc}")

print(f"conexões entre locais com a origem certa: {ok}; com a origem ERRADA: {len(ruins)}")
for r in ruins[:10]:
    print("  ERRADA:", r)
sys.exit(1 if ruins or ok == 0 else 0)
