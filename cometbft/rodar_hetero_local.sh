#!/bin/bash
# Skeen entre plataformas, local (16/11/2026; cometbft/PLANO_HETERO.md): canal1 = rede Fabric (4 orderers, SmartBFT, consenter
# Skeen do Algoritmo 3 com provas assinadas) e canal2 = blockchain CometBFT (4 validadores, skeen-node). O coordenador do
# CometBFT escuta o fio do Fabric em 127.0.0.1:28050–28053 (o alg3Addr do canal2 sem SKEEN_HOSTS); o Fabric verifica os
# certificados do CometBFT com SKEEN_FOREIGN (chaves ed25519 dos validadores) e o CometBFT verifica os do Fabric com os
# certificados x509 dos orderers. O cliente é o cliente_e2e do Fabric com -cmt: a tx com destino no canal2 vai pela RPC
# do CometBFT com o mesmo TxID. No fim, verifica_hetero.py junta a ordem de entrega dos dois lados.
# SO_CMT=<fração>: parte das globais vai só ao CometBFT, que reenvia o START ao Fabric (relay CometBFT → Fabric).
# Uso (da raiz do projeto): [W=4] [CROSS=0.3] [DUR=20] [SO_CMT=0] [RATE=<tx/s>] bash cometbft/rodar_hetero_local.sh <dir de trabalho> <orderer> <cliente_e2e>
set -u
BASEDIR=$(cd "$(dirname "$0")/.." && pwd); cd "$BASEDIR"
T=${1:?dir}; BIN=${2:?binário do orderer}; CLI=${3:?binário do cliente_e2e}
W=${W:-4}; CROSS=${CROSS:-0.3}; DUR=${DUR:-20}; FABBIN=${FABBIN:-bin}; CMT=cometbft/bin
# pkill -x (nome do processo, não a linha de comando): o caminho do orderer aparece nos argumentos deste script
pkill -9 -x orderer_alg3_lo 2>/dev/null; pkill -9 -x skeen-node 2>/dev/null; sleep 1
rm -rf "$T"; mkdir -p "$T/seq" "$T/cmt_out" "$T/ledger" "$T/tt"
ORD=crypto-config/ordererOrganizations/example.com/orderers

# --- canal2: blockchain CometBFT ---
"$CMT/cometbft" testnet --v 4 --o "$T/cmt" --starting-ip-address 127.0.2.1 > /dev/null 2>&1 || { echo "ERRO testnet"; exit 1; }
for n in 0 1 2 3; do
    h="$T/cmt/node$n"; ip="127.0.2.$((n+1))"
    sed -i 's/"chain_id": "[^"]*"/"chain_id": "skeen-canal2"/' "$h/config/genesis.json"
    sed -i "s#^laddr = \"tcp://0.0.0.0:26656\"#laddr = \"tcp://$ip:26656\"#; s#^laddr = \"tcp://127.0.0.1:26657\"#laddr = \"tcp://$ip:26657\"#; s#^pprof_laddr = .*#pprof_laddr = \"\"#; s#^addr_book_strict = true#addr_book_strict = false#; s#^allow_duplicate_ip = false#allow_duplicate_ip = true#; s#^timeout_commit = .*#timeout_commit = \"100ms\"#; s#^log_level = .*#log_level = \"error\"#; s#^flush_throttle_timeout = .*#flush_throttle_timeout = \"10ms\"#; s#^peer_gossip_sleep_duration = .*#peer_gossip_sleep_duration = \"10ms\"#" "$h/config/config.toml"
done
FOREIGN=$(python3 - "$T" "$ORD" <<'EOF'
import json, sys
T, ORD = sys.argv[1], sys.argv[2]
reps, keys = [], []
for n in range(4):
    k = json.load(open(f"{T}/cmt/node{n}/config/priv_validator_key.json"))["pub_key"]["value"]
    keys.append(k)
    reps.append({"addr": f"127.0.2.{n+1}:27000", "pubkey": k})
pems = []
for nd in ("orderer", "orderer2", "orderer3", "orderer4"):
    import glob
    pems.append(open(glob.glob(f"{ORD}/{nd}.example.com/msp/signcerts/*.pem")[0]).read())
fab = {"canal1": {"addrs": [f"127.0.0.1:{27050 + r}" for r in range(4)], "pems": pems}}
for n in range(4):
    cfg = {"chain": "canal2", "listen": f"127.0.2.{n+1}:27000", "f": 1,
           "chains": {"canal2": {"replicas": reps, "rpc": "http://127.0.2.1:26657"}}, "fabric": fab}
    if n == 0:
        cfg["wire_listen"] = [f"127.0.0.1:{28050 + r}" for r in range(4)]
    json.dump(cfg, open(f"{T}/cmt/node{n}/skeen.json", "w"), indent=1)
print("canal2=" + ",".join(keys))
EOF
)
for n in 0 1 2 3; do
    "$CMT/skeen-node" --home "$T/cmt/node$n" --skeen-config "$T/cmt/node$n/skeen.json" --out-dir "$T/cmt_out" > "$T/cmt/node$n.log" 2>&1 &
    echo $! >> "$T/pids"
done

# --- canal1: rede Fabric (4 orderers SmartBFT, Algoritmo 3, provas assinadas) ---
export FABRIC_CFG_PATH=$BASEDIR
"$FABBIN"/configtxgen -profile SkeenBFTRealGenesis1 -channelID canal1 -outputBlock "$T/canal1.block" &> "$T/configtxgen.log" || { echo "ERRO configtxgen"; exit 1; }
for r in 1 2 3 4; do
    nd=orderer; [ $r -ne 1 ] && nd=orderer$r
    td="$BASEDIR/$ORD/$nd.example.com/tls"; md="$BASEDIR/$ORD/$nd.example.com/msp"; k=$((r-1))
    mkdir -p "$T/ledger/r$r/wal" "$T/ledger/r$r/snap"
    (
        export ORDERER_GENERAL_LISTENPORT=$((7050 + r - 1)) ORDERER_ADMIN_LISTENADDRESS=127.0.0.1:$((9443 + k*10))
        export ORDERER_OPERATIONS_LISTENADDRESS=127.0.0.1:$((8443 + k*10))
        export ORDERER_ADMIN_TLS_ENABLED=true ORDERER_ADMIN_TLS_CERTIFICATE="$td/server.crt" ORDERER_ADMIN_TLS_PRIVATEKEY="$td/server.key" ORDERER_ADMIN_TLS_CLIENTROOTCAS="$td/ca.crt"
        export ORDERER_GENERAL_LOCALMSPID=OrdererMSP ORDERER_GENERAL_LOCALMSPDIR="$md"
        export ORDERER_GENERAL_TLS_ENABLED=true ORDERER_GENERAL_TLS_PRIVATEKEY="$td/server.key" ORDERER_GENERAL_TLS_CERTIFICATE="$td/server.crt" ORDERER_GENERAL_TLS_ROOTCAS="$td/ca.crt"
        export ORDERER_FILELEDGER_LOCATION="$T/ledger/r$r/ledger" ORDERER_CONSENSUS_WALDIR="$T/ledger/r$r/wal" ORDERER_CONSENSUS_SNAPDIR="$T/ledger/r$r/snap"
        unset ORDERER_GENERAL_GENESISFILE
        export SKEEN_ALG3=1 SKEEN_BFT_ENGINE=smartbft SKEEN_BFT_REPLICA_ID=$r SKEEN_BFT_REPLICA_COUNT=4 SKEEN_NUM_SHARDS=2
        export SKEEN_ALG3_PROOF=sig SKEEN_FOREIGN="$FOREIGN" SKEEN_ALG3_GROUPS=canal1,canal2 SKEEN_ALG3_STATS=1 SKEEN_ALG3_SEQDIR="$T/seq"
        export SKEEN_TXTIMING=1 SKEEN_TXTIMING_SAMPLE=1 SKEEN_TXTIMING_DIR="$T/tt"   # marcas O/D/C do Fabric (dump por SIGUSR1)
        export FABRIC_LOGGING_SPEC='error:orderer.consensus.skeen=info:SKEEN-BFT=info'
        exec "$BIN"
    ) &> "$T/fab_r$r.log" &
    echo $! >> "$T/pids"; echo $! >> "$T/fabpids"
done
trap 'kill -9 $(cat "$T/pids") 2>/dev/null' EXIT
sleep 8
for r in 1 2 3 4; do
    nd=orderer; [ $r -ne 1 ] && nd=orderer$r; td="$BASEDIR/$ORD/$nd.example.com/tls"; k=$((r-1))
    "$FABBIN"/osnadmin channel join --channelID canal1 --config-block "$T/canal1.block" -o "127.0.0.1:$((9443 + k*10))" \
        --ca-file "$td/ca.crt" --client-cert "$td/server.crt" --client-key "$td/server.key" &>> "$T/join.log" || echo "WARN join r$r"
done
sleep 15
printf 'canal1=127.0.0.1:7050\ncanal2=127.0.0.1:8050\n' > "$T/cfg.txt"
echo "cliente: workers=$W cross=$CROSS dur=${DUR}s so_cmt=${SO_CMT:-0} rate=${RATE:-0}"
T0=$(date +%s%N)
SKEEN_TXTIMING_DIR_CLIENTE="$T/tt" SKEEN_TXTIMING_SAMPLE=1 "$CLI" -workers "$W" -cross "$CROSS" -shards 2 -payload 128 -config "$T/cfg.txt" -out-sum "" -duration "$DUR" -warmup 3 \
    -rpc-timeout 20 -bft-replicas 1 -cmt "canal2=http://127.0.2.2:26657" -so-cmt "${SO_CMT:-0}" -rate "${RATE:-0}" > "$T/cliente.txt" 2>&1
grep -E 'TPS global|Txs erro|Txs ok' "$T/cliente.txt" | head -4
sleep ${DRENO:-15}
kill -USR1 $(cat "$T/fabpids") 2>/dev/null   # dump das marcas do Fabric
for i in $(seq 1 30); do sleep 1; [ "$(find "$T/tt" -name 'txtiming_[0-9]*.csv' -size +0 | wc -l)" -ge 4 ] && break; done; sleep 2
kill -9 $(cat "$T/pids") 2>/dev/null; trap - EXIT; sleep 1
# entregas do CometBFT (réplica 0) como marcas D do canal2, no formato do txtiming, para o mesmo agregador das baterias
python3 - "$T" <<'PY'
import sys
T = sys.argv[1]
with open(f"{T}/tt/txtiming_cmt.csv", "w") as o:
    for l in open(f"{T}/cmt_out/canal2_0.txt"):
        p = l.split()
        if len(p) == 5:
            o.write(f"D,canal2,{p[1]},{int(p[4]) * 1000}\n")
PY
echo "LATENCIA (envio no cliente → entrega no último destino; janela de ${DUR}s após 3s de aquecimento):"
SKEEN_TXTIMING_SAMPLE=1 python3 resultados/scripts/txtiming_agrega_d.py "$T/tt" "$T0" 3 "$DUR" | tr ' ' '\n' | grep -E '^(d_tps|incompletas_d|x_n|x_sd_avg|x_sd_p50|x_sd_p99|i_sd_avg|i_sd_p50)=' | tr '\n' ' '; echo
grep -h 'panic\|fatal error' "$T"/fab_r*.log "$T"/cmt/node*.log | sort | uniq -c | head -5
python3 cometbft/verifica_hetero.py "$T/seq" "$T/cmt_out"
