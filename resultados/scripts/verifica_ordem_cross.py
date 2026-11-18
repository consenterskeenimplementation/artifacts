#!/usr/bin/env python3
"""
Verifica as propriedades do multicast atômico a partir dos dumps do txtiming de um teste LOCAL com amostragem 1/1
(SKEEN_TXTIMING_SAMPLE=1): cada réplica grava a marca D (Alg. 1, linha 24/25) de cada tx que entrega, e a ordem
das marcas D numa réplica é a ordem de entrega dela. Só as tx cross (2+ destinos) passam pelo Algoritmo 1/3 no
Skeen-CFT/BFT; as intra vão direto ao consenso e não entram na verificação.
  integridade  — nenhuma réplica entrega a mesma tx duas vezes, nem uma tx de cujo destino não faz parte
  acordo       — réplicas do mesmo shard entregam a mesma sequência (uma pode ser prefixo da outra: réplica morta)
  ordem        — a precedência induzida por todas as réplicas de todos os shards é acíclica (ordem global, a
                 propriedade que vale para |dest| ≥ 3; para 2 destinos ela se reduz à ordem par a par)
  completude   — toda tx cross enviada pelo cliente foi entregue em todos os destinos (por ≥ 1 réplica viva)
O arquivo de dump é txtiming_<porta>_<pid>.csv; a porta identifica (shard, réplica): 7050 + (s−1)·1000 + (r−1).
Uso: python3 verifica_ordem_cross.py <dir_dumps> [réplicas mortas, ex.: canal1/r1]
"""
import glob
import os
import re
import sys
from collections import defaultdict

d = sys.argv[1]
mortas = set(sys.argv[2:])
seqs = {}            # (canal, réplica) -> [txid cross na ordem de entrega]
seqs_todas = {}      # (canal, réplica) -> [(ns, txid)] de todas as entregas (cross e locais), na ordem
enviadas = set()     # tx cross com marca S no cliente
envio = {}           # tx -> marca S (envio no cliente), cross e locais
d_min = {}           # tx -> 1ª marca D em qualquer réplica de qualquer shard
o_min = {}           # tx -> 1ª chegada ao Broadcast (marca O)


def eh_cross(tx):
    return tx.startswith("CROSS_") and len(tx.split("_")[1].split("-")) >= 2


for a in glob.glob(os.path.join(d, "txtiming_*.csv")):
    m = re.match(r"txtiming_(\d+)_\d+\.csv$", os.path.basename(a))
    regs = []
    for linha in open(a):
        p = linha.rstrip("\n").split(",")
        if len(p) != 4 or not p[2].startswith("CROSS_"):
            continue
        k, canal, tx, ns = p
        if k == "S":
            envio[tx] = min(envio.get(tx, int(ns)), int(ns))
        elif k == "O":
            o_min[tx] = min(o_min.get(tx, int(ns)), int(ns))
            if eh_cross(tx):
                enviadas.add(tx)
        elif k == "D" and m:
            regs.append((int(ns), canal, tx))
            d_min[tx] = min(d_min.get(tx, int(ns)), int(ns))
    if m and regs:
        porta = int(m.group(1))
        s, r = (porta - 7050) // 1000 + 1, (porta - 7050) % 1000 + 1
        regs.sort()
        seqs[(f"canal{s}", f"r{r}")] = [tx for _, _, tx in regs if eh_cross(tx)]
        seqs_todas[(f"canal{s}", f"r{r}")] = [(ns, tx) for ns, _, tx in regs]
        assert all(c == f"canal{s}" for _, c, _ in regs), f"porta {porta} gravou outro canal"


# Sequência exata de entrega (SKEEN_ALG3_SEQDIR, 08/10): cada réplica grava os txIDs na ordem em que os entregou. Quando
# existe, substitui a ordem inferida dos instantes das marcas D (que o salto de relógio do WSL embaralha) nas checagens
# de integridade, acordo e ordem; o tempo real continua pelos instantes, com a margem de salto.
seq_gravada = False
for a in glob.glob(os.path.join(d, "seq_canal*_r*.txt")):
    m = re.match(r"seq_(canal\d+)_r(\d+)\.txt$", os.path.basename(a))
    if m:
        linhas = open(a).read().split("\n")
        linhas = linhas[:-1]  # o último item é "" (arquivo termina em \n) ou uma linha cortada pelo buffer: descarta
        seqs[(m.group(1), f"r{m.group(2)}")] = [tx for tx in linhas if eh_cross(tx)]
        seq_gravada = True


def dest(tx):
    return tx.split("_")[1].split("-")


erros = []
# integridade
for (c, r), seq in sorted(seqs.items()):
    if len(seq) != len(set(seq)):
        erros.append(f"integridade: {c}/{r} entregou tx repetida ({len(seq) - len(set(seq))})")
    fora = [t for t in seq if c not in dest(t)]
    if fora:
        erros.append(f"integridade: {c}/{r} entregou {len(fora)} tx que não são para {c}, ex. {fora[0]}")
# acordo dentro do shard
por_shard = defaultdict(dict)
for (c, r), seq in seqs.items():
    por_shard[c][r] = seq
# Compara só as tx presentes nas duas réplicas: o dump do txtiming percorre 64 baldes (por hash do txID) enquanto
# a réplica continua entregando, então o retrato de uma réplica atrasada NÃO é um prefixo (pode ter uma entrega
# nova e faltar uma antiga). A ordem relativa das tx em comum, porém, tem de ser idêntica.
for c, reps in sorted(por_shard.items()):
    maior = max(reps.values(), key=len)
    for r, seq in sorted(reps.items()):
        comuns = set(seq) & set(maior)
        a = [t for t in seq if t in comuns]
        b = [t for t in maior if t in comuns]
        if a != b:
            i = next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)
            erros.append(f"acordo: {c}/{r} diverge na posição {i} das tx em comum: {a[i]} vs {b[i]}")
# ordem global: grafo de precedência (arestas entre entregas consecutivas de cada réplica) deve ser acíclico
suc = defaultdict(set)
for seq in seqs.values():
    for a, b in zip(seq, seq[1:]):
        suc[a].add(b)
cor, ciclo = {}, None
for raiz in list(suc):
    if raiz in cor:
        continue
    pilha = [(raiz, iter(suc[raiz]))]
    cor[raiz] = 1
    while pilha and not ciclo:
        no, it = pilha[-1]
        prox = next(it, None)
        if prox is None:
            cor[no] = 2
            pilha.pop()
        elif cor.get(prox) == 1:
            ciclo = (no, prox)
        elif prox not in cor:
            cor[prox] = 1
            pilha.append((prox, iter(suc[prox])))
if ciclo:
    erros.append(f"ordem: ciclo de precedência entre shards passando por {ciclo[0]} → {ciclo[1]}")
# ordem par a par (redundante com o grafo, mas dá a mensagem legível): tx comuns a dois shards na mesma ordem
canais = sorted(por_shard)
for i, a in enumerate(canais):
    for b in canais[i + 1:]:
        sa = [t for t in max(por_shard[a].values(), key=len) if b in dest(t)]
        sb = [t for t in max(por_shard[b].values(), key=len) if a in dest(t)]
        comuns = set(sa) & set(sb)
        if [t for t in sa if t in comuns] != [t for t in sb if t in comuns]:
            erros.append(f"ordem: {a} e {b} entregaram as tx comuns em ordens diferentes")
# tempo real (C10): se a cross X já tinha sido entregue em algum shard ANTES de o cliente enviar a local L,
# então X vem antes de L no shard de L. Percorre cada réplica de trás para frente guardando a menor 1ª entrega
# (em qualquer shard) das cross que aparecem DEPOIS — se ela é anterior ao envio de L, a ordem fere o tempo real.
# Margem contra salto de relógio: a checagem compara relógios de processos diferentes (cliente × réplicas). O WSL2
# ressincroniza o relógio com o Windows e às vezes ele SALTA (visto em 06/10: ~533 ms para trás, deixando a marca S
# de algumas tx DEPOIS da chegada ao orderer, o que é causalmente impossível). O maior S − O observado mede o salto; só
# conta como violação o que passa dessa margem (+ 5 ms).
saltos = [envio[t] - o_min[t] for t in envio if t in o_min and envio[t] > o_min[t]]
margem = (max(saltos) + 5_000_000) if saltos else 0
n_locais, viol_tr = 0, []
for (c, r), seq in sorted(seqs_todas.items()):
    menor_depois = None
    for ns, tx in reversed(seq):
        if eh_cross(tx):
            dm = d_min.get(tx)
            if dm is not None and (menor_depois is None or dm < menor_depois[0]):
                menor_depois = (dm, tx)
        else:
            n_locais += 1
            s_l = envio.get(tx)
            if s_l is not None and menor_depois is not None and menor_depois[0] + margem < s_l:
                viol_tr.append(f"{c}/{r}: local {tx} entregue antes de {menor_depois[1]}, que já tinha sido entregue "
                               f"{(s_l - menor_depois[0]) / 1e6:.1f} ms antes de a local ser enviada")
if viol_tr:
    erros.append(f"tempo real: {len(viol_tr)} violações, ex. {viol_tr[0]}")
# completude
entregue = defaultdict(set)
for (c, r), seq in seqs.items():
    if f"{c}/{r}" in mortas:
        continue
    for t in seq:
        entregue[t].add(c)
faltam = [t for t in enviadas if not set(dest(t)) <= entregue[t]]
ndest = defaultdict(int)
for t in enviadas:
    ndest[len(dest(t))] += 1

print(f"réplicas com dump: {len(seqs)}  ({', '.join(f'{c}/{r}:{len(s)}' for (c, r), s in sorted(seqs.items()))})")
print(f"tx cross enviadas: {len(enviadas)}  por nº de destinos: {dict(sorted(ndest.items()))}")
print(f"tx cross entregues em todos os destinos: {len(enviadas) - len(faltam)}  faltam: {len(faltam)}"
      + (f"  ex. {sorted(faltam)[:3]}" if faltam else ""))
print(f"arestas de precedência verificadas: {sum(len(v) for v in suc.values())}")
if saltos:
    print(f"AVISO relógio: {len(saltos)} tx com envio no cliente DEPOIS da chegada ao orderer (salto de até "
          f"{max(saltos) / 1e6:.0f} ms); checagem de tempo real feita com essa margem")
print(f"entregas de tx locais verificadas quanto ao tempo real: {n_locais}"
      + ("" if n_locais else " (nenhuma marca D de tx local: binário sem a C10?)"))
# Com salto de relógio, a ordem das marcas D DENTRO de uma réplica também fica embaralhada (o dump não guarda a ordem
# de registro, só o instante): acordo e ordem por esse critério ficam inconclusivos — a prova passa a ser o hash
# interno da sequência entregue (ordem=N/hash do SKEEN-ALG3-STATS), que o teste_alg3_local_dest.sh compara.
inconclusivos = []
print("fonte da ordem: " + ("sequência gravada pelas réplicas (SKEEN_ALG3_SEQDIR)" if seq_gravada else "instantes das marcas D"))
if saltos and not seq_gravada:
    inconclusivos = [e for e in erros if e.startswith(("acordo", "ordem"))]
    erros = [e for e in erros if e not in inconclusivos]
for e in inconclusivos:
    print("INCONCLUSIVO (relógio)", e)
for e in erros:
    print("ERRO", e)
print("VEREDITO:", "SEM DADOS (nenhuma tx cross enviada: run inválido)" if not enviadas else "FALHA" if erros else ("INCOMPLETO" if faltam else
      ("INCONCLUSIVO (salto de relógio: conferir o hash de ordem)" if inconclusivos else "OK")))
