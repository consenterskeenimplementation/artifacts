#!/usr/bin/env python3
"""Verificador do Skeen entre blockchains CometBFT (16/11/2026). Lê as sequências de entrega de cada réplica
(<dir>/<blockchain>_<réplica>.txt: "seq id final_clock final_grupo t_us") e os envios do cliente (envios.txt:
"id t_us destinos") e confere, como o teste_alg3_local_dest.sh do Fabric:
  ACORDO       todas as réplicas de uma blockchain entregam a mesma sequência;
  INTEGRIDADE  nenhuma tx entregue duas vezes, nem fora dos seus destinos, nem sem ter sido enviada;
  COMPLETUDE   toda tx enviada é entregue em todos os seus destinos (em todas as réplicas);
  ORDEM        ordem global atômica: o grafo de precedência das tx globais, unindo as sequências de todas as
               blockchains, é acíclico (com 2 blockchains: mesma ordem relativa nas duas);
  ORDEM_FINAL  em cada blockchain, a entrega segue o timestamp final crescente.
Latência = entrega no último destino (réplica 0 de cada blockchain) − envio no cliente. Grava resumo.json.
Uso: verifica.py <dir das saídas> <envios.txt>"""
import collections
import glob
import json
import os
import re
import statistics
import sys

d, envios = sys.argv[1], sys.argv[2]
seqs = collections.defaultdict(dict)  # blockchain -> réplica -> [(id, (clock, grupo), t)]
for arq in glob.glob(os.path.join(d, "*_[0-9]*.txt")):
    m = re.match(r"(.+)_(\d+)\.txt$", os.path.basename(arq))
    if not m:
        continue
    lin = []
    for l in open(arq):
        p = l.split()
        if len(p) == 5:
            lin.append((p[1], (int(p[2]), p[3]), int(p[4])))
    seqs[m.group(1)][int(m.group(2))] = lin
env = {}
for l in open(envios):
    p = l.split()
    if len(p) == 3:
        env[p[0]] = (int(p[1]), p[2].split(","))

ok_geral = True
res = {"blockchains": {}, "checagens": {}}


def marca(nome, ok, det=""):
    global ok_geral
    ok_geral &= ok
    res["checagens"][nome] = {"ok": ok, "detalhe": det}
    print(f"{nome:12} {'OK' if ok else 'FALHA'}  {det}")


# ACORDO
falhas = []
for g, reps in sorted(seqs.items()):
    ids = {r: [x[0] for x in s] for r, s in reps.items()}
    ref = ids[min(ids)]
    for r, s in ids.items():
        if s != ref:
            n = next((i for i, (a, b) in enumerate(zip(s, ref)) if a != b), min(len(s), len(ref)))
            falhas.append(f"{g}: réplica {r} difere da {min(ids)} na posição {n} ({len(s)} × {len(ref)} entregas)")
    res["blockchains"][g] = {"replicas": len(reps), "entregas": len(ref)}
marca("ACORDO", not falhas, "; ".join(falhas) or f"{len(seqs)} blockchains, sequências idênticas em todas as réplicas")

# INTEGRIDADE
falhas = []
for g, reps in seqs.items():
    for r, s in reps.items():
        c = collections.Counter(x[0] for x in s)
        dup = [i for i, k in c.items() if k > 1]
        fora = [x[0] for x in s if x[0] not in env or g not in env[x[0]][1]]
        if dup:
            falhas.append(f"{g}/{r}: {len(dup)} duplicadas")
        if fora:
            falhas.append(f"{g}/{r}: {len(fora)} entregues sem envio ou fora dos destinos")
marca("INTEGRIDADE", not falhas, "; ".join(falhas) or "sem duplicatas nem entregas indevidas")

# COMPLETUDE
falt = collections.Counter()
for i, (t, dest) in env.items():
    for g in dest:
        for r, s in seqs.get(g, {}).items():
            if i not in {x[0] for x in s}:
                falt[g] += 1
                break
marca("COMPLETUDE", not falt, ", ".join(f"{g}: {k} faltando" for g, k in falt.items()) or f"{len(env)} tx enviadas, todas entregues em todos os destinos")

# ORDEM global atômica (grafo de precedência entre globais acíclico)
glob_ids = {i for i, (t, dest) in env.items() if len(dest) > 1}
arestas = collections.defaultdict(set)
for g, reps in seqs.items():
    s = [x[0] for x in reps[min(reps)] if x[0] in glob_ids]
    for a, b in zip(s, s[1:]):
        arestas[a].add(b)
# Kahn
grau = collections.Counter()
for a, bs in arestas.items():
    for b in bs:
        grau[b] += 1
nos = set(arestas) | {b for bs in arestas.values() for b in bs}
fila = [n for n in nos if grau[n] == 0]
vistos = 0
while fila:
    n = fila.pop()
    vistos += 1
    for b in arestas.get(n, ()):
        grau[b] -= 1
        if grau[b] == 0:
            fila.append(b)
marca("ORDEM", vistos == len(nos), f"{len(glob_ids)} tx globais; grafo de precedência {'acíclico' if vistos == len(nos) else 'com ciclo'}")

# ORDEM_FINAL: entrega em ordem crescente de final
falhas = []
for g, reps in seqs.items():
    s = reps[min(reps)]
    if any(not (a[1] < b[1]) for a, b in zip(s, s[1:])):
        falhas.append(g)
marca("ORDEM_FINAL", not falhas, "fora de ordem em " + ",".join(falhas) if falhas else "entregas em final[m] crescente")

# TEMPO_REAL (linearizabilidade, como o C10 do teste do Fabric): se m1 foi entregue em todos os destinos (réplica 0)
# antes de m2 ser enviada, então em toda blockchain que as duas compartilham m1 vem antes de m2
fim = {}
ent0 = {g: {x[0]: x[2] for x in reps[min(reps)]} for g, reps in seqs.items()}
for i, (t, dest) in env.items():
    ds = [ent0.get(g, {}).get(i) for g in dest]
    if ds and None not in ds:
        fim[i] = max(ds)
viol = 0
for g, reps in seqs.items():
    s = [x[0] for x in reps[min(reps)]]
    menor = float("inf")  # menor fim[] entre as tx que vêm depois na sequência
    for i in reversed(s):
        if i in env and menor < env[i][0]:
            viol += 1
        menor = min(menor, fim.get(i, float("inf")))
marca("TEMPO_REAL", viol == 0, f"{viol} violações" if viol else "nenhuma tx entregue depois de outra que começou só após a sua conclusão")

# latência (réplica 0 de cada blockchain)
ent = {g: {x[0]: x[2] for x in reps[0]} for g, reps in seqs.items() if 0 in reps}
lat = {"globais": [], "locais": []}
for i, (t, dest) in env.items():
    ds = [ent[g].get(i) for g in dest if g in ent]
    if ds and None not in ds:
        lat["globais" if len(dest) > 1 else "locais"].append((max(ds) - t) / 1000)
for k, v in lat.items():
    if v:
        v.sort()
        r = {"n": len(v), "media_ms": round(statistics.mean(v), 1), "p50_ms": round(v[len(v) // 2], 1),
             "p99_ms": round(v[min(len(v) - 1, int(0.99 * len(v)))], 1)}
        res[f"latencia_{k}"] = r
        print(f"LATENCIA {k:8} n={r['n']} media={r['media_ms']} ms p50={r['p50_ms']} ms p99={r['p99_ms']} ms")
res["veredito"] = "OK" if ok_geral else "FALHA"
print("VEREDITO", res["veredito"])
json.dump(res, open(os.path.join(d, "resumo.json"), "w"), ensure_ascii=False, indent=1)
sys.exit(0 if ok_geral else 1)
