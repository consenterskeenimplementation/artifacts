#!/bin/bash
# Teste LOCAL de fumaça do Algoritmo 3 (SKEEN_ALG3=1) com tx cross para 3 ou 4 destinos 
# (4 shards × 2/3/4 destinos).
# Sobe NSH shards (CFT: 3 réplicas etcdraft; BFT: 4 réplicas SmartBFT), binário do fork (bin/orderer_alg3_local, preparar.sh),
# amostragem 1/1 do txtiming e, no fim, verifica integridade, acordo, ordem global
# acíclica e completude das tx cross (resultados/scripts/verifica_ordem_cross.py). Sem netem, sem medição de desempenho.
# MODE=puro: Skeen-puro (Algoritmo 1, 1 réplica por shard, sem consenso), para a C10 (tx local com timestamp).
# Uso (da raiz do projeto): MODE=cft|bft|puro DEST=3 [NSH=4] [KILL=1] bash resultados/sh/teste_alg3_local_dest.sh [workers/shard] [cross] [duração]
#   KILL=1 mata com -9, no meio do run, o coordenador do canal1: o líder etcdraft (CFT) ou a r1 (BFT; o cliente então
#   passa a tentar r1..r4, -bft-replicas 4). Binário: $SP/[VARIANTE/]orderer_alg3_local (padrão SP=bin/; o nome do
#   binário não pode aparecer na linha de comando de quem chama, senão o pkill do script mata o chamador).
#   Resultado e logs em /tmp/alg3dest/.
export LC_NUMERIC=C
BASEDIR=$(cd "$(dirname "$0")/../.." && pwd)
cd "$BASEDIR"
WPS=${1:-10}; CROSS=${2:-0.5}; DUR=${3:-30}; WARMUP=5
MODE=${MODE:-cft}; DEST=${DEST:-3}; NSH=${NSH:-4}; KILL=${KILL:-}
SP=${SP:-$BASEDIR/bin}
FABBIN=${FABBIN:-$BASEDIR/bin}   # configtxgen/osnadmin (preparar.sh)
BIN=$SP/${VARIANTE:+$VARIANTE/}orderer_alg3_local; CLI=${CLIENTE:-$SP/cliente_e2e_bin}
case $MODE in bft) NREP=4; PROFILE=SkeenBFTRealGenesis ;; puro) NREP=1; PROFILE=SkeenGenesis ;; *) NREP=3; PROFILE=SkeenCFTRealGenesis ;; esac
T=/tmp/alg3dest; DATA=$T/ledger
pkill -9 -f '[o]rderer_alg3_local' 2>/dev/null; sleep 1
rm -rf $T && mkdir -p $T/tt $DATA
export FABRIC_CFG_PATH=$BASEDIR
for s in $(seq 1 $NSH); do
    prof="$PROFILE$s"; [ "$MODE" = puro ] && prof=$PROFILE
    "$FABBIN"/configtxgen -profile "$prof" -channelID "canal$s" -outputBlock "$T/canal$s.block" &>$T/configtxgen$s.log \
        || { echo "configtxgen falhou (canal$s)"; exit 1; }
done

start() {
    local s=$1 r=$2 nd; [ "$r" -eq 1 ] && nd="orderer" || nd="orderer${r}"
    local td="$BASEDIR/crypto-config/ordererOrganizations/example.com/orderers/${nd}.example.com/tls"
    local md="$BASEDIR/crypto-config/ordererOrganizations/example.com/orderers/${nd}.example.com/msp"
    local k=$(( (s-1)*NREP + (r-1) ))
    mkdir -p "$DATA/s${s}r${r}/wal" "$DATA/s${s}r${r}/snap"
    (
        export ORDERER_GENERAL_LISTENPORT=$(( 7050 + (s-1)*1000 + (r-1) ))
        export ORDERER_ADMIN_LISTENADDRESS=127.0.0.1:$(( 9443 + k*10 ))
        export ORDERER_OPERATIONS_LISTENADDRESS=127.0.0.1:$(( 8443 + k*10 ))
        export ORDERER_ADMIN_TLS_ENABLED=true ORDERER_ADMIN_TLS_CERTIFICATE="$td/server.crt"
        export ORDERER_ADMIN_TLS_PRIVATEKEY="$td/server.key" ORDERER_ADMIN_TLS_CLIENTROOTCAS="$td/ca.crt"
        export ORDERER_GENERAL_LOCALMSPID=OrdererMSP ORDERER_GENERAL_LOCALMSPDIR="$md"
        export ORDERER_GENERAL_TLS_ENABLED=true ORDERER_GENERAL_TLS_PRIVATEKEY="$td/server.key"
        export ORDERER_GENERAL_TLS_CERTIFICATE="$td/server.crt" ORDERER_GENERAL_TLS_ROOTCAS="$td/ca.crt"
        export ORDERER_FILELEDGER_LOCATION="$DATA/s${s}r${r}/ledger"
        export ORDERER_CONSENSUS_WALDIR="$DATA/s${s}r${r}/wal" ORDERER_CONSENSUS_SNAPDIR="$DATA/s${s}r${r}/snap"
        unset ORDERER_GENERAL_GENESISFILE
        case $MODE in
            bft) export SKEEN_ALG3=1 SKEEN_BFT_ENGINE=smartbft SKEEN_BFT_REPLICA_ID=$r SKEEN_BFT_REPLICA_COUNT=4 ;;
            cft) export SKEEN_ALG3=1 SKEEN_CFT_ENGINE=etcdraft SKEEN_CFT_REPLICA_ID=$r SKEEN_CFT_REPLICA_COUNT=3 ;;
        esac
        export SKEEN_ALG3_STATS=1 SKEEN_TXTIMING=1 SKEEN_TXTIMING_SAMPLE=1 SKEEN_TXTIMING_DIR=$T/tt SKEEN_ALG3_SEQDIR=$T/tt SKEEN_NUM_SHARDS=$NSH
        export FABRIC_LOGGING_SPEC='error:orderer.consensus.skeen=info:SKEEN-BFT=info:orderer.consensus.etcdraft=info'
        exec "$BIN"
    ) &> "$T/s${s}r${r}.log" &
    echo $! > "$T/s${s}r${r}.pid"
}

for s in $(seq 1 $NSH); do for r in $(seq 1 $NREP); do start $s $r; done; done
sleep 8
for s in $(seq 1 $NSH); do for r in $(seq 1 $NREP); do
    nd="orderer"; [ "$r" -ne 1 ] && nd="orderer${r}"
    td="$BASEDIR/crypto-config/ordererOrganizations/example.com/orderers/${nd}.example.com/tls"
    k=$(( (s-1)*NREP + (r-1) ))
    "$FABBIN"/osnadmin channel join --channelID "canal$s" --config-block "$T/canal$s.block" \
        -o "127.0.0.1:$(( 9443 + k*10 ))" --ca-file "$td/ca.crt" --client-cert "$td/server.crt" \
        --client-key "$td/server.key" &>>$T/join.log || echo "WARN join s${s}r${r}"
done; done
sleep 15
: > $T/cfg.txt
for s in $(seq 1 $NSH); do
    b=$(( 7050 + (s-1)*1000 ))
    if [ "$MODE" != cft ]; then echo "canal$s=127.0.0.1:$b" >> $T/cfg.txt   # puro: 1 réplica; SmartBFT só repassa ao líder após 5 s: cliente no r1
    else echo "canal$s=127.0.0.1:$b,127.0.0.1:$((b+1)),127.0.0.1:$((b+2))" >> $T/cfg.txt; fi
done
NB=1; [ -n "$KILL" ] && NB=4
case $MODE in bft) REPS="-bft-replicas $NB" ;; puro) REPS="" ;; *) REPS="-cft-replicas 3 -probe-leader -probe-count 5" ;; esac

MORTA=""
if [ -n "$KILL" ]; then
    if [ "$MODE" = bft ]; then MORTA=r1
    else  # líder atual do etcdraft no canal1 (última troca registrada em qualquer réplica do canal1)
        L=$(grep -ho 'Raft leader changed: [0-9]* -> [0-9]*' $T/s1r*.log | tail -1 | awk '{print $NF}'); MORTA=r${L:-1}
    fi
    ( sleep $(( WARMUP + DUR / 3 )); kill -9 "$(cat $T/s1${MORTA}.pid)" 2>/dev/null && echo "KILL canal1/$MORTA $(date +%T)" >> $T/eventos.txt ) &
fi

W=$(( WPS * NSH )); T0=$(date +%s%N)
SKEEN_TXTIMING_DIR_CLIENTE=$T/tt SKEEN_TXTIMING_SAMPLE=1 "$CLI" -workers "$W" -cross "$CROSS" -shards "$DEST" -payload 128 \
    -config $T/cfg.txt -out-sum "" -duration "$DUR" -warmup "$WARMUP" -rpc-timeout 20 $REPS > $T/cliente.txt 2>&1
sleep ${DRAIN:-10}   # deixa as tx cross em voo terminarem antes do dump
pkill -USR1 -f '[o]rderer_alg3_local'
VIVAS=$(( NSH * NREP - ${MORTA:+1}0 / 10 ))
ant=""; for i in $(seq 1 60); do   # espera os dumps de todas as réplicas vivas ficarem não vazios e estáveis
    sleep 2
    n=$(find $T/tt -name 'txtiming_[0-9]*.csv' -size +0 | wc -l); tam=$(du -sb $T/tt | cut -f1)
    [ "$n" -ge "$VIVAS" ] && [ "$tam" = "$ant" ] && break; ant=$tam
done
echo "== MODE=$MODE NSH=$NSH DEST=$DEST cross=$CROSS workers=$W dur=${DUR}s kill=${MORTA:-não}"
grep -E 'TPS global|Txs erro|Txs ok' $T/cliente.txt | head -6
cat $T/eventos.txt 2>/dev/null
SKEEN_TXTIMING_SAMPLE=1 python3 resultados/scripts/txtiming_agrega_d.py $T/tt "$T0" "$WARMUP" "$DUR" | tr ' ' '\n' | grep -E '^(d_tps|incompletas_d|x_n|x_inc|x_sd_p50|x_d_real)=' | tr '\n' ' '; echo
python3 resultados/scripts/verifica_ordem_cross.py $T/tt ${MORTA:+canal1/$MORTA}
grep -h 'panic\|fatal error\|DATA RACE' $T/s*r*.log | sort | uniq -c | head -5
# conferência autoritativa: o hash da sequência entregue (ordem=N/hash do SKEEN-ALG3-STATS) tem de ser igual nas
# réplicas de cada shard que entregaram a mesma quantidade (o dump do txtiming é só um retrato)
if [ "$MODE" != puro ]; then
    for s in $(seq 1 $NSH); do
        for r in $(seq 1 $NREP); do grep -h "STATS\]" $T/s${s}r${r}.log | tail -1 | grep -o "ordem=[0-9]*/[0-9a-f]*"; done | sort | uniq -c |
            awk -v s=$s '{n[$2]=$1; split($2,p,"/"); c[p[1]]++} END {for (k in c) if (c[k]>1) {print "ERRO HASH canal" s ": hashes diferentes com a mesma contagem"; exit}; printf "hash de ordem canal%s: ok (%d valor(es))\n", s, length(n)}'
    done
fi
pkill -9 -f '[o]rderer_alg3_local'
