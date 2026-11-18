#!/usr/bin/env python3
"""Gera a página de validação do Skeen sobre CometBFT (16–17/11/2026) com uma ou duas execuções:
  --cmt <dir out>  execução CometBFT × CometBFT (rodar_local.sh: rastros, envios.txt, resumo.json);
  --het <dir>      execução Fabric × CometBFT (rodar_hetero_local.sh: cmt_out/ com os rastros do CometBFT, tt/ com as
                   marcas do txtiming do Fabric e do cliente, resumo_hetero.json do verifica_hetero.py).
Os trechos de código são lidos do código atual, com o número da linha.
Uso (de cometbft/): python3 gera_pagina.py [--cmt <dir>] [--het <dir>] <html de saída>"""
import collections
import glob
import json
import re
import statistics
import sys

args = sys.argv[1:]
saida = args[-1]
opt = {args[i]: args[i + 1] for i in range(0, len(args) - 1, 2)}


def estat(v):
    v = sorted(v)
    return {"n": len(v), "media_ms": round(statistics.mean(v), 1), "p50_ms": round(v[len(v) // 2], 1),
            "p99_ms": round(v[min(len(v) - 1, int(0.99 * len(v)))], 1)} if v else None


def amostra(txs, n=12):
    txs = sorted(txs, key=lambda x: x["t0"])
    meio = len(txs) // 3
    return [{"id": t["id"], "eventos": t["eventos"]} for t in txs[meio:meio + n]]


def rastros(pasta):
    ev = collections.defaultdict(list)
    for f in glob.glob(f"{pasta}/*_trace.jsonl"):
        for l in open(f):
            e = json.loads(l)
            if e["r"] in (0, 1):
                ev[e["x"]].append(e)
    return ev


execs = []
if "--cmt" in opt:
    d = opt["--cmt"]
    ev = rastros(d)
    env = {}
    for l in open(f"{d}/envios.txt"):
        i, t, dest = l.split()
        env[i] = (int(t), dest)
    txs = []
    for i, (t0, dest) in env.items():
        es = ev.get(i, [])
        if "," not in dest or not any(e["ev"] == "DELIVER" for e in es):
            continue
        lst = sorted(({"t": round((e["t"] - t0) / 1000, 1), "ev": e["ev"], "c": e["c"], "r": e["r"], "v": e["v"]}
                      for e in es), key=lambda e: e["t"])
        txs.append({"id": i, "t0": t0, "eventos": lst})
    res = json.load(open(f"{d}/resumo.json"))
    n = len(res["blockchains"])
    execs.append({"nome": f"CometBFT × CometBFT ({n} blockchains)", "fabric": [], "txs": amostra(txs), "resumo": res,
                  "descricao": f"{n} blockchains CometBFT independentes ({', '.join(sorted(res['blockchains']))}), cada uma "
                               "com 4 validadores (f = 1) e o próprio consenso BFT; cada global vai a 2 delas, sorteadas.",
                  "config": f"{n} blockchains × 4 validadores (f = 1), globais com 2 destinos sorteados, execução local sem "
                            "WAN, CometBFT com timeout_commit de 100 ms e flush_throttle_timeout e "
                            "peer_gossip_sleep_duration de 10 ms."})
if "--het" in opt:
    d = opt["--het"]
    ev = rastros(f"{d}/cmt_out")
    S, O, Df, Dc = {}, {}, {}, {}
    for f in glob.glob(f"{d}/tt/txtiming_*.csv"):
        for l in open(f):
            p = l.rstrip("\n").split(",")
            if len(p) != 4:
                continue
            k, canal, tx, ns = p[0], p[1], p[2], int(p[3])
            if k == "S":
                S[tx] = min(S.get(tx, ns), ns)
            elif k == "O" and canal == "canal1":
                O[tx] = min(O.get(tx, ns), ns)
            elif k == "D" and canal == "canal1":
                Df[tx] = min(Df.get(tx, ns), ns)  # 1ª réplica do Fabric a entregar
            elif k == "D" and canal == "canal2":
                Dc[tx] = ns
    eh_global = lambda i: bool(re.match(r"CROSS_(canal1-canal2|canal2-canal1)_", i))
    lat_g, lat_l, txs = [], [], []
    for i, s0 in S.items():
        if eh_global(i):
            if i in Df and i in Dc:
                lat_g.append((max(Df[i], Dc[i]) - s0) / 1e6)
                es = [{"t": round((e["t"] * 1000 - s0) / 1e6, 1), "ev": e["ev"], "c": e["c"], "r": e["r"], "v": e["v"]}
                      for e in ev.get(i, [])]
                if i in O:
                    es.append({"t": round((O[i] - s0) / 1e6, 1), "ev": "F_O", "c": "canal1", "r": 0, "v": {}})
                es.append({"t": round((Df[i] - s0) / 1e6, 1), "ev": "F_D", "c": "canal1", "r": 0, "v": {}})
                if any(e["ev"] == "DELIVER" for e in es):
                    txs.append({"id": i, "t0": s0, "eventos": sorted(es, key=lambda e: e["t"])})
        else:
            dd = Df.get(i) or Dc.get(i)
            if dd:
                lat_l.append((dd - s0) / 1e6)
    res = json.load(open(f"{d}/resumo_hetero.json"))
    res["latencia_globais"], res["latencia_locais"] = estat(lat_g), estat(lat_l)
    execs.append({"nome": "Fabric × CometBFT (entre plataformas)", "fabric": ["canal1"], "txs": amostra(txs), "resumo": res,
                  "descricao": "canal1 é uma rede Hyperledger Fabric (4 orderers, SmartBFT, consenter Skeen) e canal2 é uma "
                               "blockchain CometBFT (4 validadores). Cada global vai às duas plataformas, com o mesmo TxID; "
                               "os coordenadores trocam certificados no formato do Fabric (ECDSA dos orderers, ed25519 dos "
                               "validadores).",
                  "config": "Fabric 3.1.4 (SmartBFT, provas assinadas) + CometBFT v0.38.26, execução local sem WAN, "
                            "cliente do Fabric com -cmt. Latência = envio no cliente → entrega na última das duas plataformas."})
    for k in ("latencia_globais", "latencia_locais"):
        if res[k] is None:
            del res[k]


def trecho(arq, inicio_re, n, raiz="cometbft/"):
    L = open(("../" if raiz == "" else "") + arq).read().split("\n")
    for k, l in enumerate(L):
        if re.search(inicio_re, l):
            return {"arquivo": raiz + arq, "linha": k + 1, "codigo": "\n".join(L[k:k + n])}
    raise SystemExit("não achei " + inicio_re)


code = {
    "START": trecho("skeenabci/app.go", r'case "S": // START_LOCAL', 9),
    "VOTE": trecho("skeenabci/skeen.go", r"^func \(n \*Net\) onVote", 30),
    "ONCERT": trecho("skeenabci/skeen.go", r"^func \(n \*Net\) onCert", 18),
    "PROGRESS": trecho("skeenabci/skeen.go", r"^func \(n \*Net\) progress", 30),
    "BATCH": trecho("skeenabci/skeen.go", r"^func \(n \*Net\) batchLoop", 40),
    "FINAL": trecho("skeenabci/app.go", r'case "F": // FINAL_LOCAL', 22),
    "CHECKTS": trecho("skeenabci/skeen.go", r"^func \(n \*Net\) checkTSCerts", 26),
    "DLV": trecho("skeenabci/app.go", r"^func \(a \*SkeenApp\) onDLV", 22),
    "TRYDELIVER": trecho("skeenabci/app.go", r"^func \(a \*SkeenApp\) tryDeliverLocked", 33),
    "VERIFY": trecho("skeenabci/skeen.go", r"^func \(n \*Net\) verifyCert", 14),
    "NEED": trecho("skeenabci/skeen.go", r"^func \(n \*Net\) onNeed", 40),
    "RETRANSMIT": trecho("skeenabci/skeen.go", r"^func \(n \*Net\) retransmit", 44),
    "WIRE": trecho("skeenabci/fabricwire.go", r"^func \(n \*Net\) handleWire", 40),
    "VERIFYFAB": trecho("skeenabci/fabricwire.go", r"^func verifyFabricCert", 22),
    "DIGEST": trecho("skeenabci/fabricwire.go", r"^func \(ct \*alg3Cert\) digest", 10),
    "FOREIGN": trecho("../fabric/orderer/consensus/skeen/chain_alg3.go", r"^func alg3VerifyForeign", 16, raiz=""),
    "FABORDER": trecho("../fabric/orderer/consensus/skeen/chain_alg3.go", r"^func \(c \*chain\) alg3Order", 30, raiz=""),
}
dados = json.dumps({"execs": execs, "codigo": code}, ensure_ascii=False).replace("</", "<\\/")
html = open(__file__.replace("gera_pagina.py", "pagina_modelo.html"), encoding="utf-8").read()
open(saida, "w", encoding="utf-8").write(html.replace("__DADOS__", dados))
print(saida, [(e["nome"], len(e["txs"])) for e in execs])
