#!/usr/bin/env python3
"""
Validação automática de cada curva medida (skill `validar-execucao`): correção dos dados e adequação do desempenho.
Lê as puxadas do servidor (arquivos com cabeçalhos ARQ_) e, para cada curva (modo, shards, % cross, destinos,
sufixo), verifica:
  [corretude]
  V1 incompletas: nenhum nível até o joelho com > 1% de tx incompletas; acima do joelho, alerta se > 5%
  V2 estabilidade: runs refeitos (pontos fora da curva) ≤ 30% do total
  V3 cobertura: todos os níveis com 3 runs
  [desempenho]
  V4 curva até o pico: a vazão não cai mais de 10% entre níveis consecutivos antes da capacidade
  V5 colapso: a vazão no último nível ≥ 70% da capacidade
  V6 ordem esperada: na mesma configuração, capacidade Skeen-puro ≥ Skeen-CFT ≥ Skeen-BFT
  V7 sem cross × nativos (1 shard): Skeen-CFT dentro de ±20% do Raft; Skeen-BFT dentro de ±25% do BFT-SMaRt
  V8 escala sem cross: capacidade com N shards ≥ 0,7·N × a de 1 shard (linearidade aproximada)
  V9 regressão: capacidade ≥ 80% da referência do binário antigo na mesma configuração, se houver (--ref)
Saída: uma linha por curva com OK ou ALERTA e o motivo. Código de saída 1 se houver ALERTA.
Uso (da raiz): python3 resultados/scripts/valida_bateria.py <puxadas...> [--ref <puxadas antigas...>] [--nativos <seqd>]
"""
import re
import statistics as st
import sys

BASE = re.compile(r"modo=(\w+) cross=[\d.]+ workers_por_shard=(\d+) run=(\d+) refeita=(\d+) .*?d_tps=(\d+) d_n=(\d+) "
                  r"incompletas_d=(\d+) sd_avg=([\d.]+|NA)ms")
SECS = [
    (re.compile(r"/dev/shm/g104d_([a-z]+)_([a-z0-9]+)/"), lambda m: (m.group(1), 1, 0, 1, m.group(2))),
    (re.compile(r"/dev/shm/g109d_([a-z]+)(\d)_([a-z0-9]+)/"), lambda m: (m.group(1), int(m.group(2)), 0, int(m.group(2)), m.group(3))),
    (re.compile(r"/dev/shm/g109dx_c(\d+)_d(\d)_([a-z]+)(\d)_([a-z0-9]+)/"),
     lambda m: (m.group(3), int(m.group(4)), pct(m.group(1)), int(m.group(2)), m.group(5))),
    (re.compile(r"/dev/shm/g109dx_c(\d+)_([a-z]+)(\d)_([a-z0-9]+)/"),  # baterias antigas (sempre 2 destinos)
     lambda m: (m.group(2), int(m.group(3)), pct(m.group(1)), 2, m.group(4))),
]


def pct(tag):
    return int(round(float(tag[0] + "." + tag[1:]) * 100)) if len(tag) > 1 else 0


def ler(arqs):
    runs = {}
    for arq in arqs:
        sec = None
        for linha in open(arq, encoding="utf-8", errors="replace"):
            if linha.startswith("ARQ_"):
                sec = None
                for rx, f in SECS:
                    m = rx.search(linha)
                    if m:
                        sec = f(m)
                        break
                continue
            m = BASE.search(linha)
            if not (sec and m and m.group(1) == sec[0] and m.group(8) != "NA"):
                continue
            dn, inc = int(m.group(6)), int(m.group(7))
            runs.setdefault(sec, {})[(int(m.group(2)), int(m.group(3)))] = dict(
                w=int(m.group(2)), tps=float(m.group(5)), inc=inc / max(1, dn + inc), refeita=int(m.group(4)) > 0)
    return runs


def curva(rs):
    out = []
    for w in sorted({r["w"] for r in rs.values()}):
        g = [r for r in rs.values() if r["w"] == w]
        out.append(dict(w=w, tps=st.median(r["tps"] for r in g), inc=st.median(r["inc"] for r in g), n=len(g)))
    return out


def capacidade(pts):
    ok = [p for p in pts if p["inc"] <= 0.01]
    if not ok:
        return None, None
    cap = max(ok, key=lambda p: p["tps"])
    return cap, next(p for p in ok if p["tps"] >= 0.9 * cap["tps"])


args = sys.argv[1:]
grupos = {"": [], "--ref": [], "--nativos": []}
atual = ""
for a in args:
    if a in grupos:
        atual = a
    else:
        grupos[atual].append(a)
novo, ref, nat = ler(grupos[""]), ler(grupos["--ref"]), ler(grupos["--nativos"])
caps = {k: capacidade(curva(v))[0] for k, v in novo.items()}
alertas = 0
NOME = {"puro": "Skeen-puro", "cft": "Skeen-CFT", "bft": "Skeen-BFT", "raft": "Raft", "bftsmart": "BFT-SMaRt"}
for k in sorted(novo, key=lambda k: (k[4], k[2], k[1], k[3], k[0])):
    modo, n, p, d, suf = k
    rs = novo[k]
    pts = curva(rs)
    cap, joe = capacidade(pts)
    motivos = []
    if cap is None:
        motivos.append("V1 todos os níveis com > 1% de incompletas")
    else:
        ruins = [p_["w"] for p_ in pts if p_["w"] <= joe["w"] and p_["inc"] > 0.01]
        if ruins:
            motivos.append(f"V1 incompletas > 1% até o joelho em {ruins}")
        altos = [f'{p_["w"]}:{100 * p_["inc"]:.0f}%' for p_ in pts if p_["w"] > joe["w"] and p_["inc"] > 0.05]
        if altos:
            motivos.append(f"V1 incompletas > 5% acima do joelho ({' '.join(altos)})")
    nref = sum(1 for r in rs.values() if r["refeita"])
    if len(rs) and nref / len(rs) > 0.30:
        motivos.append(f"V2 instável: {nref} de {len(rs)} runs refeitos")
    faltando = [p_["w"] for p_ in pts if p_["n"] < 3]
    if faltando:
        motivos.append(f"V3 níveis com < 3 runs: {faltando}")
    if cap:
        antes = [p_ for p_ in pts if p_["w"] <= cap["w"]]
        quedas = [f'{a["w"]}→{b["w"]}' for a, b in zip(antes, antes[1:]) if b["tps"] < 0.9 * a["tps"]]
        if quedas:
            motivos.append(f"V4 vazão cai antes do pico ({' '.join(quedas)})")
        if pts[-1]["tps"] < 0.7 * cap["tps"]:
            motivos.append(f"V5 colapso: último nível com {100 * pts[-1]['tps'] / cap['tps']:.0f}% da capacidade")
        outros = {m: caps.get((m, n, p, d, suf)) for m in ("puro", "cft", "bft")}
        if modo == "cft" and outros["puro"] and cap["tps"] > 1.05 * outros["puro"]["tps"]:
            motivos.append("V6 Skeen-CFT acima do Skeen-puro")
        if modo == "bft" and outros["cft"] and cap["tps"] > 1.05 * outros["cft"]["tps"]:
            motivos.append("V6 Skeen-BFT acima do Skeen-CFT")
        if p == 0 and n == 1 and modo in ("cft", "bft"):
            nativo = "raft" if modo == "cft" else "bftsmart"
            cn = [capacidade(curva(v))[0] for kk, v in nat.items() if kk[0] == nativo and kk[1] == 1]
            if cn and cn[0]:
                tol = 0.20 if modo == "cft" else 0.25
                r = cap["tps"] / cn[0]["tps"]
                if abs(r - 1) > tol:
                    motivos.append(f"V7 {NOME[modo]} = {r:.2f}× o {NOME[nativo]} nativo (tolerância ±{int(tol * 100)}%)")
        if p == 0 and n > 1:
            c1 = caps.get((modo, 1, 0, 1, suf))
            if c1 and cap["tps"] < 0.7 * n * c1["tps"]:
                motivos.append(f"V8 escala: {n} shards = {cap['tps'] / c1['tps']:.2f}× 1 shard (esperado ≥ {0.7 * n:.1f}×)")
        cr = [capacidade(curva(v))[0] for kk, v in ref.items() if kk[:4] == (modo, n, p, d)]
        if cr and cr[0] and cap["tps"] < 0.8 * cr[0]["tps"]:
            motivos.append(f"V9 regressão: {cap['tps']:,.0f} TPS = {100 * cap['tps'] / cr[0]['tps']:.0f}% do binário antigo ({cr[0]['tps']:,.0f})")
    cfg = f"{NOME.get(modo, modo):10s} {n} shard(s) {p:2d}% cross {d} dest [{suf}]"
    resumo = f"cap {cap['tps']:8,.0f} TPS joelho {joe['w']:4d} cli/shard" if cap else "sem capacidade válida"
    if motivos:
        alertas += 1
        print(f"ALERTA {cfg} | {resumo} | " + "; ".join(motivos))
    else:
        print(f"OK     {cfg} | {resumo} | {len(rs)} runs, {nref} refeitos")
print(f"-- {len(novo)} curvas, {alertas} com ALERTA")
sys.exit(1 if alertas else 0)
