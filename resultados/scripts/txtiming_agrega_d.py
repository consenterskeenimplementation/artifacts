#!/usr/bin/env python3
"""
Agrega os dumps do txtiming de UM run medindo no ponto de medida do artigo: Algoritmo 1 do artigo, depois da
linha 24 (delivered ← delivered ∪ {m}) e antes da linha 25 (deliver(m)) — marca 'D'. No fast-path intra-shard a
marca D fica no ponto equivalente (tx entregue à fila de blocos, antes do commit).
  vazão D      = tx (amostradas ×SAMPLE) cuja marca D em todos os destinos caiu na janela [t0+warmup, t0+duração]
  latência S→D = D (último destino) − envio no cliente (S), para tx enviadas na janela
  latência O→D = D (último destino) − chegada ao Broadcast (O)
  incompletas  = tx enviadas na janela sem D em todos os destinos até o dump
Também imprime, como referência, a vazão e a latência até o commit do bloco (marca C).
Uso: python3 txtiming_agrega_d.py <dir_dumps> <t0_ns> <warmup_s> <duracao_s> [--apaga]
"""
import glob
import os
import sys

d, t0, wu, dur = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
ini, fim = t0 + int(wu * 1e9), t0 + int(dur * 1e9)
amostra = int(os.environ.get("SKEEN_TXTIMING_SAMPLE", "32"))
marcas = {"S": {}, "O": {}, "D": {}, "C": {}}  # S, O: tx -> ns (mínimo); D, C: tx -> {canal: ns}
canal_chegada = {}
arqs = glob.glob(os.path.join(d, "txtiming_*.csv"))
for a in arqs:
    with open(a) as f:
        for linha in f:
            p = linha.rstrip("\n").split(",")
            if len(p) != 4 or p[0] not in marcas:
                continue
            k, canal, tx, ns = p[0], p[1], p[2], int(p[3])
            if k in ("S", "O"):
                if tx not in marcas[k] or ns < marcas[k][tx]:
                    marcas[k][tx] = ns
                    if k == "O":
                        canal_chegada[tx] = canal
            else:
                c = marcas[k].setdefault(tx, {})
                if canal not in c or ns < c[canal]:
                    c[canal] = ns


# Protocolos sem Algoritmo 1 no caminho da tx (Raft e BFT-SMaRt nativos; Skeen-CFT/BFT com tx intra-shard, que vão
# direto ao consenso) não têm marca D: o ponto equivalente a "entregue, antes do deliver" é a decisão do consenso
# gravada no bloco (marca C). d_origem diz quantas tx usaram D de fato e quantas usaram C.
n_d_real = len(marcas["D"])
d_reais = set(marcas["D"])  # tx que tiveram marca D de verdade (as demais usam o commit)
for tx, c in marcas["C"].items():
    if tx not in marcas["D"]:
        marcas["D"][tx] = dict(c)
n_d_commit = len(marcas["D"]) - n_d_real


def destinos(tx):
    p = tx.split("_")
    if len(p) >= 2 and p[1]:
        return p[1].split("-")
    return [canal_chegada[tx]] if tx in canal_chegada else []


def fim_em(k, tx):
    """instante em que a tx atingiu a marca k em TODOS os destinos (None se faltou algum)"""
    c, dest = marcas[k].get(tx, {}), destinos(tx)
    return max(c[x] for x in dest) if dest and all(x in c for x in dest) else None


def resumo(v):
    if not v:
        return "NA", "NA", "NA"
    v = sorted(v)
    return f"{sum(v) / len(v):.2f}", f"{v[len(v) // 2]:.2f}", f"{v[min(len(v) - 1, int(0.99 * len(v)))]:.2f}"


def vazao(k):
    n = sum(1 for tx in marcas[k] if (t := fim_em(k, tx)) is not None and ini <= t <= fim)
    return n * amostra / ((fim - ini) / 1e9)


s_d, o_d, s_c, inc = [], [], [], 0
s_d_x, s_d_i, inc_x, x_d_real = [], [], 0, 0  # separação cross (2+ destinos) × intra (1 destino)
s_d_par = {}  # latência S→D das tx cross por conjunto de destinos (teste de WAN: par de regiões), chave ordenada
# TXTIMING_AMOSTRAS_DIR: grava também cada latência S→D amostrada (tipo x/i, destinos, ms) em <dir>/<tag>_<t0>.csv,
# para a CDF; tag = nome da pasta de dumps do run
amostras = []
for tx, ts in marcas["S"].items():
    if not (ini <= ts <= fim):
        continue
    cruzada = len(destinos(tx)) > 1
    td = fim_em("D", tx)
    if td is None:
        inc += 1
        inc_x += cruzada
        continue
    s_d.append((td - ts) / 1e6)
    (s_d_x if cruzada else s_d_i).append((td - ts) / 1e6)
    if cruzada:
        s_d_par.setdefault("-".join(sorted(destinos(tx))), []).append((td - ts) / 1e6)
    amostras.append(f"{'x' if cruzada else 'i'},{'-'.join(sorted(destinos(tx)))},{(td - ts) / 1e6:.3f}")
    x_d_real += cruzada and tx in d_reais
    if tx in marcas["O"]:
        o_d.append((td - marcas["O"][tx]) / 1e6)
    tc = fim_em("C", tx)
    if tc is not None:
        s_c.append((tc - ts) / 1e6)
sda, sdp50, sdp99 = resumo(s_d)
oda, odp50, odp99 = resumo(o_d)
sca, _, scp99 = resumo(s_c)
print(f"d_tps={vazao('D'):.0f} d_n={len(s_d)} incompletas_d={inc} sd_avg={sda}ms sd_p50={sdp50}ms sd_p99={sdp99}ms "
      f"od_avg={oda}ms od_p50={odp50}ms od_p99={odp99}ms commit_tps={vazao('C'):.0f} sc_avg={sca}ms sc_p99={scp99}ms "
      f"d_origem=D:{n_d_real},C:{n_d_commit} "
      f"x_n={len(s_d_x)} x_inc={inc_x} x_sd_avg={resumo(s_d_x)[0]}ms x_sd_p50={resumo(s_d_x)[1]}ms x_sd_p99={resumo(s_d_x)[2]}ms "
      f"x_d_real={x_d_real} i_n={len(s_d_i)} i_sd_avg={resumo(s_d_i)[0]}ms i_sd_p50={resumo(s_d_i)[1]}ms"
      + ("" if not s_d_par else " pares=" + ",".join(f"{k}:{resumo(v)[0]}:{resumo(v)[1]}:{len(v)}"
                                                    for k, v in sorted(s_d_par.items()))))  # destinos:avg:p50:n
if os.environ.get("TXTIMING_AMOSTRAS_DIR"):
    os.makedirs(os.environ["TXTIMING_AMOSTRAS_DIR"], exist_ok=True)
    with open(os.path.join(os.environ["TXTIMING_AMOSTRAS_DIR"], f"{os.path.basename(os.path.normpath(d))}_{t0}.csv"), "w") as f:
        f.write("tipo,destinos,lat_ms\n" + "\n".join(amostras) + "\n")
if "--apaga" in sys.argv:
    for a in arqs:
        os.remove(a)
