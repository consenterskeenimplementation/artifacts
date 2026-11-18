#!/bin/bash
# teste109x_diag.sh = teste109x_dest.sh com logs das réplicas guardados (resultados_d/logs_<dir>/s<s>r<r>.log) e SKEEN_ALG3_STATS=1
# (estado das tx pendentes por fase a cada 2 s), para diagnóstico (05/10). Não usar em bateria de medição.
# teste109x_dest.sh = teste109x_d.sh com o NÚMERO DE DESTINOS de cada tx cross configurável (DEST, padrão 2), para a
# matriz pedida em 05/10: 4 shards × 2/3/4 destinos × 5–20% de cross (docs/PROXIMOS_EXPERIMENTOS.md). Ainda NÃO usado
# em bateria. Diferenças em relação ao teste109x_d.sh (que fica intacto, porque está rodando):
#   - DEST entra no cliente (-shards $DEST), nos nomes (/dev/shm/g109dx_c<cross>_d<DEST>_<modo><N>...) e em cada linha;
#   - ORDERER (nome do binário em fabric/build/bin, padrão orderer_alg3d) permite trocar o binário sem editar o script.
# Antes de usar com DEST ≥ 3: ver o teste local resultados/sh/teste_alg3_local_dest.sh (05/10) e a correção C11 do
# docs/PLANO_CORRECAO_ALG3.md (corrida que trava o coordenador; sem ela o run pode congelar).
# Uso: DEST=3 CROSS=0.1 teste109x_dest.sh <puro|cft|bft> <N shards>   (env: WORKERS_LIST, NRUNS, DURATION, MAX_REFAZ, SUFIXO, ORDERER)
# Modo refazimento: PARES="w:r ..." RODADA0=3 DEST=3 CROSS=0.1 teste109x_dest.sh <modo> <N>
set -e
export LC_NUMERIC=C
MODE=${1:?uso: $0 <puro|cft|bft|raft|bftsmart>}
NSHARDS=${2:?uso: $0 <puro|cft|bft> <N shards>}
BASEDIR=$(cd "$(dirname "$0")" && pwd)
FABRIC_DIR="$BASEDIR/../fabric"
BIN="$FABRIC_DIR/build/bin/${ORDERER:-orderer_alg3d}"
cd "$BASEDIR"

RC=6
case $MODE in
    cft) NREP=3; PROFILE=SkeenCFTRealGenesis ;;
    bft) NREP=4; PROFILE=SkeenBFTRealGenesis ;;
    puro) NREP=1; PROFILE=SkeenGenesis ;;
    raft) NREP=3; PROFILE=RaftChannel3 ;;
    bftsmart) NREP=4; PROFILE=SmartBFTChannel ;;
    *) echo "modo inválido: $MODE"; exit 1 ;;
esac
DURATION=${DURATION:-60}
NRUNS=${NRUNS:-3}
WARMUP=10
CROSS=${CROSS:-0.1}; CROSS_LIST=$CROSS
DEST=${DEST:-2}
[ "$DEST" -ge 2 ] && [ "$DEST" -le "$NSHARDS" ] || { echo "DEST=$DEST inválido para $NSHARDS shards"; exit 1; }
WORKERS_LIST=${WORKERS_LIST:-"25 100 200 300 400 600 1000"}
MAX_REFAZ=${MAX_REFAZ:-2}
BOOT_WAIT=$(( 25 + 5 * NSHARDS )); [ "$BOOT_WAIT" -lt 35 ] && BOOT_WAIT=35
[ "$MODE" = puro ] && BOOT_WAIT=10
CTAG=$(echo "$CROSS_LIST" | tr -d '.')
SHM=/dev/shm/g109dx_c$(echo $CROSS | tr -d .)_d${DEST}_${MODE}${NSHARDS}${SUFIXO:-}
DATA="$BASEDIR/ledger_g109dx_d${DEST}_${MODE}${NSHARDS}"
DISCO="$BASEDIR/resultados_d/g109dx_c$(echo $CROSS | tr -d .)_d${DEST}_${MODE}${NSHARDS}${SUFIXO:-}_$(date +%d%m_%H%M).txt"; mkdir -p "$BASEDIR/resultados_d"
mkdir -p "$SHM" "$SHM/tt"
RES="$SHM/resultado.txt"
# logs das réplicas em DISCO: o /dev/shm do servidor tem só 64 MB e encheu com eles em 05/10 (abortou a matriz)
LOGD="$BASEDIR/resultados_d/logs_$(basename $SHM)"; rm -rf "$LOGD"; mkdir -p "$LOGD"

gidx() { echo $(( ($1-1)*NREP + ($2-1) )); }
NODE0_REPL=$(( 72 / RC ))
TOTAL_REPL=$(( NSHARDS * NREP ))
SPILL=$(( TOTAL_REPL > NODE0_REPL ? TOTAL_REPL - NODE0_REPL : 0 ))
replica_cores() { local g=$(gidx $1 $2); if [ "$g" -lt "$NODE0_REPL" ]; then echo "$((g*RC))-$((g*RC + RC - 1))"; else local o=$(( g - NODE0_REPL )); echo "$((80 + o*RC))-$((80 + o*RC + RC - 1))"; fi; }
NODE1_BASE=$(( 80 + SPILL * RC ))
CLIENT_MAXCORES=$(( 160 - NODE1_BASE ))
client_cores_n() { local n=$(( ($1 + 24) / 25 )); [ "$n" -gt "$CLIENT_MAXCORES" ] && n=$CLIENT_MAXCORES; echo $n; }
client_cores_span() { local n=$(client_cores_n "$1"); echo "$NODE1_BASE-$((NODE1_BASE + n - 1))"; }
# porta gRPC: Skeen usa réplicas consecutivas (7050,7051,...); os nativos, uma por milhar (7050,8050,...)
gport() { case $MODE in raft|bftsmart) echo $(( 7050 + ($2-1)*1000 ));; *) echo $(( 7050 + ($1-1)*1000 + ($2-1) ));; esac; }

apply_latency_lan() {
    python3 latsetter.py unset lo &>/dev/null || true
    { echo "Port,Latency"
      for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
          g=$(gport $s $r); echo "$g,2"; echo "$((g+10000)),2"; echo "$((g+20000)),2"  # porta do Algoritmo 3 (alg3PortOffset=20000 desde 05/10)
      done; done; } > "$SHM/lat.csv"
    python3 latsetter.py set-local "$SHM/lat.csv" --protocol tcp &>/dev/null
}

make_start_script() {
    local s=$1 r=$2
    local nd; [ "$r" -eq 1 ] && nd="orderer" || nd="orderer${r}"
    local td="$BASEDIR/crypto-config/ordererOrganizations/example.com/orderers/${nd}.example.com/tls"
    local md="$BASEDIR/crypto-config/ordererOrganizations/example.com/orderers/${nd}.example.com/msp"
    local k=$(gidx $s $r)
    mkdir -p "$DATA/s${s}r${r}/wal" "$DATA/s${s}r${r}/snapshot"
    {
        echo "#!/bin/bash"
        echo "export GOMAXPROCS=$RC"
        echo "export FABRIC_CFG_PATH='$BASEDIR'"
        echo "export ORDERER_GENERAL_LISTENPORT=$(gport $s $r)"
        echo "export ORDERER_ADMIN_LISTENADDRESS=127.0.0.1:$(( 9443 + k*10 ))"
        echo "export ORDERER_OPERATIONS_LISTENADDRESS=127.0.0.1:$(( 8443 + k*10 ))"
        echo "export ORDERER_ADMIN_TLS_ENABLED=true"
        echo "export ORDERER_ADMIN_TLS_CERTIFICATE='$td/server.crt'"
        echo "export ORDERER_ADMIN_TLS_PRIVATEKEY='$td/server.key'"
        echo "export ORDERER_ADMIN_TLS_CLIENTROOTCAS='$td/ca.crt'"
        echo "export ORDERER_GENERAL_LOCALMSPID=OrdererMSP"
        echo "export ORDERER_GENERAL_LOCALMSPDIR='$md'"
        echo "export ORDERER_GENERAL_TLS_ENABLED=true"
        echo "export ORDERER_GENERAL_TLS_PRIVATEKEY='$td/server.key'"
        echo "export ORDERER_GENERAL_TLS_CERTIFICATE='$td/server.crt'"
        echo "export ORDERER_GENERAL_TLS_ROOTCAS='$td/ca.crt'"
        echo "export ORDERER_FILELEDGER_LOCATION='$DATA/s${s}r${r}/ledger'"
        echo "export ORDERER_CONSENSUS_WALDIR='$DATA/s${s}r${r}/wal'"
        echo "export ORDERER_CONSENSUS_SNAPDIR='$DATA/s${s}r${r}/snapshot'"
        echo "unset ORDERER_GENERAL_GENESISFILE"
        case $MODE in
            cft) echo "export SKEEN_ALG3=1 SKEEN_CFT_ENGINE=etcdraft SKEEN_CFT_REPLICA_ID=$r SKEEN_CFT_REPLICA_COUNT=$NREP" ;;
            bft) echo "export SKEEN_ALG3=1 SKEEN_BFT_ENGINE=smartbft SKEEN_BFT_REPLICA_ID=$r SKEEN_BFT_REPLICA_COUNT=$NREP" ;;
        esac
        echo "export SKEEN_NOWRITE=1 SKEEN_WAL_NO_WRITE=1 SKEEN_SMARTBFT_WAL_NO_WRITE=1"
        echo "export SKEEN_TXTIMING=1 SKEEN_TXTIMING_SAMPLE=32 SKEEN_TXTIMING_DIR='$SHM/tt'"
        echo "export SKEEN_ALG3_STATS=1 FABRIC_LOGGING_SPEC='error:orderer.consensus.skeen=info:SKEEN-BFT=info:orderer.consensus.etcdraft=info'"
        echo "exec $BIN"
    } > "$SHM/s${s}r${r}.sh"
    chmod +x "$SHM/s${s}r${r}.sh"
}

init_shards() {
    pkill -9 -f '[b]uild/bin/orderer' 2>/dev/null || true
    sleep 8
    rm -rf "$DATA" && mkdir -p "$DATA"
    export FABRIC_CFG_PATH="$BASEDIR"
    for s in $(seq 1 $NSHARDS); do
        local prof="$PROFILE$s"; case $MODE in puro|raft|bftsmart) prof=$PROFILE;; esac
        "$FABRIC_DIR/build/bin/configtxgen" -profile "$prof" -channelID "canal${s}" -outputBlock "$SHM/canal${s}.block" &>/dev/null
    done
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
        make_start_script "$s" "$r"
        taskset -c "$(replica_cores $s $r)" bash "$SHM/s${s}r${r}.sh" &>"$LOGD/s${s}r${r}.log" &
    done; done
    sleep $BOOT_WAIT
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
        local nd; [ "$r" -eq 1 ] && nd="orderer" || nd="orderer${r}"
        local td="$BASEDIR/crypto-config/ordererOrganizations/example.com/orderers/${nd}.example.com/tls"
        "$FABRIC_DIR/build/bin/osnadmin" channel join --channelID "canal${s}" --config-block "$SHM/canal${s}.block" \
            -o "127.0.0.1:$(( 9443 + $(gidx $s $r)*10 ))" --ca-file "$td/ca.crt" --client-cert "$td/server.crt" \
            --client-key "$td/server.key" &>/dev/null || echo "WARN: join shard${s} r${r} falhou" >> "$RES"
    done; done
    [ "$MODE" = puro ] && sleep 3 || sleep $BOOT_WAIT
    : > "$SHM/cfg.txt"
    for s in $(seq 1 $NSHARDS); do
        case $MODE in
            cft|raft) echo "canal${s}=127.0.0.1:$(gport $s 1),127.0.0.1:$(gport $s 2),127.0.0.1:$(gport $s 3)" >> "$SHM/cfg.txt" ;;
            *) echo "canal${s}=127.0.0.1:$(gport $s 1)" >> "$SHM/cfg.txt" ;; # puro; BFT: líder r1
        esac
    done
}

resumir() {
    python3 -c "
import sys, re
t = sys.stdin.read()
g = lambda p, s: (re.search(p, s).group(1) if re.search(p, s) else 'NA')
i = re.search(r'INTRA-SHARD.*?(?=CROSS|\$)', t, re.DOTALL)
x = re.search(r'CROSS-SHARD.*?(?=INTRA|\$)', t, re.DOTALL)
u = i.group(0) if i else ''
v = x.group(0) if x else ''
print('TPS_total=%s erros=%s intra_ack_avg=%sms cross_ack_avg=%sms cross_ack_p99=%sms' % (g(r'TPS global\s*:\s*([\d.]+)', t),
      g(r'Txs erro\s*:\s*(\d+)', t), g(r'Avg:\s*([\d.]+)', u), g(r'Avg:\s*([\d.]+)', v), g(r'P99:\s*([\d.]+)', v)))
"
}

REPS=""; case $MODE in cft|raft) REPS="-cft-replicas 3 -probe-leader -probe-count 5";; esac
reg() { echo "$1" >> "$RES"; echo "$1" >> "$DISCO"; }
roda_par() {  # $1 = workers por shard, $2 = run, $3 = rodada de refazimento (0 = original)
    local wps=$1 run=$2 refeita=$3
    init_shards
    local W=$((wps * NSHARDS)); local nc=$(client_cores_n "$W"); local T0=$(date +%s%N)
    local out=$(SKEEN_TXTIMING_DIR_CLIENTE="$SHM/tt" SKEEN_TXTIMING_SAMPLE=32 GOMAXPROCS=$nc taskset -c "$(client_cores_span $W)" ./cliente_e2e_bin \
        -workers "$W" -cross "$CROSS" -shards "$DEST" -payload 128 -config "$SHM/cfg.txt" -out-sum "" \
        -duration "$DURATION" -warmup "$WARMUP" -rpc-timeout 30 $REPS 2>/dev/null) || true
    sleep 5
    pkill -USR1 -f '[b]uild/bin/orderer' 2>/dev/null || true
    sleep 3
    local tt=$(SKEEN_TXTIMING_SAMPLE=32 python3 txtiming_agrega_d.py "$SHM/tt" "$T0" "$WARMUP" "$DURATION" --apaga 2>/dev/null)
    reg "modo=$MODE cross=$CROSS workers_por_shard=$wps run=$run refeita=$refeita shards=$NSHARDS dest=$DEST total=$W cores_cliente=$nc  $(echo "$out" | resumir)  $tt  hora=$(date +%T)"
}
apply_latency_lan
R0=0
if [ -n "${PARES:-}" ]; then
    R0=${RODADA0:-3}
    reg "REFAZ_FINAL $(date '+%d/%m %T') modo=$MODE ${NSHARDS} shards dest=$DEST pares={$PARES}"
    for par in $PARES; do roda_par "${par%%:*}" "${par##*:}" "$R0"; done
else
    reg "INICIO $(date '+%d/%m %T') modo=$MODE ${NSHARDS} shards, CROSS=$CROSS (cada tx cross em $DEST shards sorteados), $(basename $BIN) md5=$(md5sum $BIN | cut -c1-12) (marca D: tx cross no tryDeliver, entre as linhas 24 e 25), Writers corrigido, ${DURATION}s (warmup ${WARMUP}s), ${NRUNS} runs, workers/shard={$WORKERS_LIST}, LAN 2ms, MAX_REFAZ=$MAX_REFAZ"
    PARES=$(for w in $WORKERS_LIST; do for r in $(seq 1 $NRUNS); do echo "$w:$r"; done; done | shuf --random-source=<(yes 20261003))
    reg "ordem: $(echo $PARES | tr '\n' ' ')"
    for par in $PARES; do roda_par "${par%%:*}" "${par##*:}" 0; done
fi
for k in $(seq 1 $MAX_REFAZ); do
    FORA=$(python3 fora_da_curva.py "$RES" --explica)
    [ -z "$FORA" ] && { reg "fora_da_curva rodada=$((R0 + k)): nenhum"; break; }
    reg "fora_da_curva rodada=$((R0 + k)): $(echo "$FORA" | tr '\n' ';')"
    for par in $(echo "$FORA" | cut -d' ' -f1); do roda_par "${par%%:*}" "${par##*:}" "$((R0 + k))"; done
done
reg "fora_da_curva final: $(python3 fora_da_curva.py "$RES" --explica | tr '\n' ';')"
python3 latsetter.py unset lo &>/dev/null || true
pkill -9 -f '[b]uild/bin/orderer' 2>/dev/null || true
rm -rf "$DATA"
reg "FIM_G109D $(date '+%d/%m %T')"
