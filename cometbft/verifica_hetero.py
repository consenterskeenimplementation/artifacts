#!/usr/bin/env python3
"""Verificador do Skeen entre plataformas (16/11/2026): canal1 = Fabric (seq_canal1_r<i>.txt: um TxID por linha, na ordem
de entrega, SKEEN_ALG3_SEQDIR) e canal2 = CometBFT (canal2_<i>.txt: "seq id final_clock final_grupo t_us").
  ACORDO       em cada plataforma, as réplicas entregam a mesma sequência (comparada até o tamanho da menor; a
               diferença de tamanho é informada, porque o processo é morto com tx ainda em voo);
  INTEGRIDADE  nenhum TxID entregue duas vezes na mesma réplica;
  COMPLETUDE   toda global (destinos canal1 e canal2) entregue num lado também foi entregue no outro;
  ORDEM        as globais comuns aparecem na MESMA ordem relativa no Fabric e no CometBFT (ordem global atômica entre
               as duas plataformas).
Uso: verifica_hetero.py <dir seq do Fabric> <dir de saída do CometBFT>"""
import glob
import json
import os
import re
import sys

fab_dir, cmt_dir = sys.argv[1], sys.argv[2]
fab = {}
for f in glob.glob(os.path.join(fab_dir, "seq_canal1_r*.txt")):
    r = re.search(r"_r(\d+)\.txt$", f).group(1)
    fab[int(r)] = [l.strip().split("\x00")[0] for l in open(f, errors="replace") if l.strip()]
cmt = {}
for f in glob.glob(os.path.join(cmt_dir, "canal2_[0-9]*.txt")):
    r = re.search(r"_(\d+)\.txt$", f).group(1)
    cmt[int(r)] = [l.split()[1] for l in open(f) if len(l.split()) == 5]
ok = True
checagens = {}


def marca(nome, bom, det):
    global ok
    ok &= bom
    chave = nome if nome not in checagens else nome + " (2)"
    checagens[chave] = {"ok": bom, "detalhe": det}
    print(f"{nome:12} {'OK' if bom else 'FALHA'}  {det}")


def eh_global(i):
    m = re.match(r"CROSS_([^_]+)_", i)
    return bool(m) and set(m.group(1).split("-")) == {"canal1", "canal2"}


for nome, reps in (("Fabric", fab), ("CometBFT", cmt)):
    if not reps:
        marca("ACORDO", False, f"{nome}: nenhuma sequência encontrada")
        continue
    ref = max(reps.values(), key=len)
    falhas = [f"r{r} diverge na posição {next(i for i, (a, b) in enumerate(zip(s, ref)) if a != b)}"
              for r, s in reps.items() if s != ref[:len(s)]]
    tams = sorted(len(s) for s in reps.values())
    marca("ACORDO", not falhas, f"{nome}: {len(reps)} réplicas, {tams[0]}–{tams[-1]} entregas" + (": " + "; ".join(falhas) if falhas else ", prefixos idênticos"))
    dup = [r for r, s in reps.items() if len(s) != len(set(s))]
    marca("INTEGRIDADE", not dup, f"{nome}: " + (f"duplicatas nas réplicas {dup}" if dup else "sem duplicatas"))

if fab and cmt:
    gf = [i for i in max(fab.values(), key=len) if eh_global(i)]
    gc = [i for i in max(cmt.values(), key=len) if eh_global(i)]
    sf, sc = set(gf), set(gc)
    so_f, so_c = sf - sc, sc - sf
    marca("COMPLETUDE", not so_f and not so_c and len(sf & sc) > 0,  # sem nenhuma global nos dois lados, o run não vale
          f"{len(sf & sc)} globais nos dois lados; só no Fabric: {len(so_f)}, só no CometBFT: {len(so_c)}")
    comuns = sf & sc
    of = [i for i in gf if i in comuns]
    oc = [i for i in gc if i in comuns]
    pos = next((k for k, (a, b) in enumerate(zip(of, oc)) if a != b), None)
    marca("ORDEM", of == oc, f"{len(of)} globais comuns na mesma ordem nas duas plataformas" if of == oc
          else f"ordem diverge na posição {pos}: Fabric {of[pos]} × CometBFT {oc[pos]}")
    nl_f = sum(1 for i in max(fab.values(), key=len) if not eh_global(i))
    nl_c = sum(1 for i in max(cmt.values(), key=len) if not eh_global(i))
    print(f"ENTREGAS     Fabric: {len(gf)} globais + {nl_f} locais; CometBFT: {len(gc)} globais + {nl_c} locais")
print("VEREDITO", "OK" if ok else "FALHA")
# resumo para a página de validação (gera_pagina.py)
json.dump({"checagens": checagens, "veredito": "OK" if ok else "FALHA",
           "blockchains": {"canal1 (Fabric)": {"replicas": len(fab), "entregas": max((len(s) for s in fab.values()), default=0)},
                           "canal2 (CometBFT)": {"replicas": len(cmt), "entregas": max((len(s) for s in cmt.values()), default=0)}}},
          open(os.path.join(os.path.dirname(os.path.abspath(cmt_dir)), "resumo_hetero.json"), "w"), ensure_ascii=False, indent=1)
sys.exit(0 if ok else 1)
