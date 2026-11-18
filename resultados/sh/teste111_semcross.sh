#!/bin/bash
# teste111_semcross.sh (10/10/2026) = teste110_pin.sh para a bateria SEM CROSS com o servidor no estado mais rápido
#: Skeen-puro, Raft, SmartBFT (BFT-SMaRt nativo), Skeen-CFT e Skeen-BFT com 1 shard e os
# Skeens também com 4 e 6 shards, todos com o mesmo binário (orderer_alg3i: o Raft e o SmartBFT nele são os originais).
# Diferenças em relação ao teste110_pin.sh:
#   - BFT (Skeen-BFT e SmartBFT nativo): líder r1 com RC=6 núcleos e seguidoras com BFT_RC_SEGUIDORA (padrão 4). No run de
#     08–10/10 as seguidoras usaram ~2 núcleos e, com 6 shards × 4 réplicas × 6 núcleos = 144, o cliente ficou com 15 e
#     limitou o Skeen-BFT; assim sobram 44 (6 shards). GOMAXPROCS = núcleos da réplica. Validado por A/B no orquestrador.
#   - Núcleos alocados em sequência: nó NUMA 0 (0–71) enquanto couber, depois o nó 1 a partir do 80; cliente nos livres do
#     nó 1 (+72–78 se forem menos de 16); núcleo 79 para os scripts de monitoração.
#   - CROSS=0 e DEST=1 por padrão (aceita 1 shard); Raft com piso de 15 s para a eleição, como o CFT.
# Uso: SUFIXO=_s WORKERS_LIST="..." teste111_semcross.sh <puro|cft|bft|raft|bftsmart> <N shards>
set -e
export LC_NUMERIC=C
MODE=${1:?uso: $0 <puro|cft|bft|raft|bftsmart>}
NSHARDS=${2:?uso: $0 <puro|cft|bft> <N shards>}
BASEDIR=$(cd "$(dirname "$0")" && pwd)
FABRIC_DIR="$BASEDIR/../fabric"
BIN="$FABRIC_DIR/build/bin/${ORDERER:-orderer_alg3i}"
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
CROSS=${CROSS:-0}; CROSS_LIST=$CROSS
DEST=${DEST:-1}
if [ "$CROSS" = 0 ]; then DEST=1; else [ "$DEST" -ge 2 ] && [ "$DEST" -le "$NSHARDS" ] || { echo "DEST=$DEST inválido para $NSHARDS shards"; exit 1; }; fi
WORKERS_LIST=${WORKERS_LIST:-"25 100 200 300 400 600 1000"}
MAX_REFAZ=${MAX_REFAZ:-2}
BOOT_WAIT=$(( 25 + 5 * NSHARDS )); [ "$BOOT_WAIT" -lt 35 ] && BOOT_WAIT=35
[ "$MODE" = puro ] && BOOT_WAIT=10
CTAG=$(echo "$CROSS_LIST" | tr -d '.')
SHM=/dev/shm/g109dx_c$(echo $CROSS | tr -d .)_d${DEST}_${MODE}${NSHARDS}${SUFIXO:-}
DATA="$BASEDIR/ledger_g109dx_d${DEST}_${MODE}${NSHARDS}"
DISCO="$BASEDIR/resultados_d/g109dx_c$(echo $CROSS | tr -d .)_d${DEST}_${MODE}${NSHARDS}${SUFIXO:-}_$(date +%d%m_%H%M).txt"; mkdir -p "$BASEDIR/resultados_d"
# dumps do txtiming em DISCO (07/10: 12 réplicas × ~4 MB estouraram os 64 MB do /dev/shm do container); só são
# escritos no fim do run (SIGUSR1), fora da janela medida, e apagados pelo agregador (--apaga)
TT=${TTDIR:-$BASEDIR/resultados_d/tt/$(basename "$SHM")}
mkdir -p "$SHM" "$TT"; rm -f "$TT"/txtiming_*
RES="$SHM/resultado.txt"

gidx() { echo $(( ($1-1)*NREP + ($2-1) )); }
RCF=${BFT_RC_SEGUIDORA:-4}
rc_de() { case $MODE in bft|bftsmart) [ "$2" -eq 1 ] && echo $RC || echo $RCF;; *) echo $RC;; esac; }
declare -A CORES NCORES
prox0=0; prox1=80
for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
    k=$(rc_de $s $r)
    if [ $(( prox0 + k )) -le 72 ]; then CORES[$s,$r]="$prox0-$(( prox0 + k - 1 ))"; prox0=$(( prox0 + k ))
    else CORES[$s,$r]="$prox1-$(( prox1 + k - 1 ))"; prox1=$(( prox1 + k )); fi
    NCORES[$s,$r]=$k
done; done
replica_cores() { echo "${CORES[$1,$2]}"; }
NODE1_BASE=$prox1
POOL_CLIENTE=$(seq -s ' ' $NODE1_BASE 159)
[ $(( 160 - NODE1_BASE )) -lt 16 ] && POOL_CLIENTE="$POOL_CLIENTE $(seq -s ' ' 72 78)"
CORE_MONITOR=79
CLIENT_MAXCORES=$(echo $POOL_CLIENTE | wc -w)
client_cores_n() { local n=$(( ($1 + 24) / 25 )); [ "$n" -gt "$CLIENT_MAXCORES" ] && n=$CLIENT_MAXCORES; echo $n; }
client_cores_span() { echo $POOL_CLIENTE | tr ' ' '\n' | head -n "$(client_cores_n "$1")" | paste -sd, ; }
plano_pinning() {  # tabela planejada, uma linha
    local s r l=""
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do l="$l s${s}r${r}=$(replica_cores $s $r)"; done; done
    echo "PINNING (seguidoras BFT: $RCF núcleos) réplicas:$l | cliente: até $CLIENT_MAXCORES núcleos {$(echo $POOL_CLIENTE | tr ' ' ',')} | monitor: $CORE_MONITOR"
}
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
        echo "export GOMAXPROCS=${NCORES[$s,$r]} SKEEN_NUM_SHARDS=$NSHARDS"
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
        echo "export SKEEN_TXTIMING=1 SKEEN_TXTIMING_SAMPLE=32 SKEEN_TXTIMING_DIR='$TT'"
        echo "export FABRIC_LOGGING_SPEC=fatal"
        echo "exec $BIN"
    } > "$SHM/s${s}r${r}.sh"
    chmod +x "$SHM/s${s}r${r}.sh"
}

declare -A PIDS
init_shards() {
    pkill -9 -f '[b]uild/bin/orderer' 2>/dev/null || true
    PIDS=()
    sleep 8
    rm -rf "$DATA" && mkdir -p "$DATA"
    export FABRIC_CFG_PATH="$BASEDIR"
    for s in $(seq 1 $NSHARDS); do
        local prof="$PROFILE$s"; case $MODE in puro|raft|bftsmart) prof=$PROFILE;; esac
        "$FABRIC_DIR/build/bin/configtxgen" -profile "$prof" -channelID "canal${s}" -outputBlock "$SHM/canal${s}.block" &>/dev/null
    done
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
        make_start_script "$s" "$r"
        taskset -c "$(replica_cores $s $r)" bash "$SHM/s${s}r${r}.sh" &>/dev/null &
        PIDS[$s,$r]=$!   # taskset → bash → exec orderer: o mesmo pid vira o orderer
    done; done
    local i ok
    for i in $(seq 1 $BOOT_WAIT); do  # portas de administração abertas em todas as réplicas
        ok=1
        for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
            (echo > /dev/tcp/127.0.0.1/$(( 9443 + $(gidx $s $r)*10 ))) 2>/dev/null || ok=0
        done; done
        [ $ok = 1 ] && break; sleep 1
    done
    sleep 1
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
        local nd; [ "$r" -eq 1 ] && nd="orderer" || nd="orderer${r}"
        local td="$BASEDIR/crypto-config/ordererOrganizations/example.com/orderers/${nd}.example.com/tls"
        "$FABRIC_DIR/build/bin/osnadmin" channel join --channelID "canal${s}" --config-block "$SHM/canal${s}.block" \
            -o "127.0.0.1:$(( 9443 + $(gidx $s $r)*10 ))" --ca-file "$td/ca.crt" --client-cert "$td/server.crt" \
            --client-key "$td/server.key" &>/dev/null || echo "WARN: join shard${s} r${r} falhou" >> "$RES"
    done; done
    if [ "$MODE" = puro ]; then sleep 3; else
        for i in $(seq 1 $BOOT_WAIT); do  # canal "active" em todas as réplicas
            ok=1
            for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
                local nd; [ "$r" -eq 1 ] && nd="orderer" || nd="orderer${r}"
                local td="$BASEDIR/crypto-config/ordererOrganizations/example.com/orderers/${nd}.example.com/tls"
                "$FABRIC_DIR/build/bin/osnadmin" channel list --channelID "canal${s}" -o "127.0.0.1:$(( 9443 + $(gidx $s $r)*10 ))" \
                    --ca-file "$td/ca.crt" --client-cert "$td/server.crt" --client-key "$td/server.key" 2>/dev/null | grep -q '"active"' || ok=0
            done; done
            [ $ok = 1 ] && break; sleep 1
        done
        case $MODE in cft|raft) sleep 15;; *) sleep 10;; esac  # piso: eleição do líder (etcdraft) / visão inicial (SmartBFT)
        reg_boot="pronto em ${i}s+piso"
    fi
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
    local span=$(client_cores_span $W)
    SKEEN_TXTIMING_DIR_CLIENTE="$TT" SKEEN_TXTIMING_SAMPLE=32 GOMAXPROCS=$nc taskset -c "$span" ./cliente_e2e_bin \
        -workers "$W" -cross "$CROSS" -shards "$DEST" -payload 128 -config "$SHM/cfg.txt" -out-sum "" \
        -duration "$DURATION" -warmup "$WARMUP" -rpc-timeout 30 $REPS > "$SHM/cli.out" 2>/dev/null &
    local CP=$!
    sleep $WARMUP
    local plano="$SHM/pin.txt" s r todos=""; : > "$plano"
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
        echo "s${s}r${r} ${PIDS[$s,$r]} $(replica_cores $s $r)" >> "$plano"; todos="$todos ${PIDS[$s,$r]}"
    done; done
    echo "cliente $CP $span" >> "$plano"
    local pin; pin=$(taskset -c $CORE_MONITOR python3 pin_cpu.py confere "$plano") || {
        reg "PIN_ERRO modo=$MODE workers_por_shard=$wps run=$run $pin"; kill -9 $CP 2>/dev/null; exit 1; }
    local ta=$(date +%s.%N) a=$(taskset -c $CORE_MONITOR python3 pin_cpu.py ticks $CP $todos)
    sleep $(( DURATION - WARMUP - 4 ))
    local tb=$(date +%s.%N) b=$(taskset -c $CORE_MONITOR python3 pin_cpu.py ticks $CP $todos)
    local cpu=$(taskset -c $CORE_MONITOR python3 pin_cpu.py uso "$(awk -v a=$ta -v b=$tb 'BEGIN{print b-a}')" "$a" "$b" $nc $RC)
    wait $CP || true
    local out=$(cat "$SHM/cli.out")
    sleep 5
    pkill -USR1 -f '[b]uild/bin/orderer' 2>/dev/null || true
    sleep 3
    local tt=$(SKEEN_TXTIMING_SAMPLE=32 python3 txtiming_agrega_d.py "$TT" "$T0" "$WARMUP" "$DURATION" --apaga 2>/dev/null)
    reg "modo=$MODE cross=$CROSS workers_por_shard=$wps run=$run refeita=$refeita shards=$NSHARDS dest=$DEST total=$W cores_cliente=$nc  $(echo "$out" | resumir)  $tt  $pin  $cpu  hora=$(date +%T)"
}
apply_latency_lan
R0=0
if [ -n "${PARES:-}" ]; then
    R0=${RODADA0:-3}
    reg "REFAZ_FINAL $(date '+%d/%m %T') modo=$MODE ${NSHARDS} shards dest=$DEST pares={$PARES}"
    for par in $PARES; do roda_par "${par%%:*}" "${par##*:}" "$R0"; done
else
    reg "INICIO $(date '+%d/%m %T') modo=$MODE ${NSHARDS} shards, CROSS=$CROSS (cada tx cross em $DEST shards sorteados entre os $NSHARDS), $(basename $BIN) md5=$(md5sum $BIN | cut -c1-12) (marca D: tx cross no tryDeliver, entre as linhas 24 e 25), Writers corrigido, ${DURATION}s (warmup ${WARMUP}s), ${NRUNS} runs, workers/shard={$WORKERS_LIST}, LAN 2ms, MAX_REFAZ=$MAX_REFAZ"
    PARES=$(for w in $WORKERS_LIST; do for r in $(seq 1 $NRUNS); do echo "$w:$r"; done; done | shuf --random-source=<(yes 20261003))
    reg "ordem: $(echo $PARES | tr '\n' ' ')"
    reg "$(plano_pinning)"
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
