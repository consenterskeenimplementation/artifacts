#!/bin/bash
# teste110w.sh (05/11/2026) = teste110_pin.sh + WAN emulada com "um canal por local". O container não permite network
# namespaces (sem CAP_SYS_ADMIN), então cada canal é um IP de loopback (127.0.s.1, local s = linha s da matriz de
# regiões) e a latência é escolhida pelo par (IP de origem, IP de destino) no lo:
#   - entre locais: matriz de latências (latsetter.py set-lo-zones; atraso fixo, sem reordenar, fila de 200 mil pacotes);
#     COND=controle: 1 ms em cada sentido entre todos os locais (RTT 2 ms, a LAN das baterias), mesma topologia;
#   - dentro do local: a mesma LAN de 2 ms do teste110_pin.sh, por IP+porta (gRPC, +10000, +20000).
# Réplicas (SKEEN_HOSTS, orderer_alg3k) e clientes (-home, cliente_e2e_wan_bin) saem pelo IP do próprio canal, para o
# filtro saber de que local vem o pacote; em todo run uma foto do `ss -tnp` confere isso (WAN_ERRO aborta a curva).
# Um cliente por local, cada um com uma fatia própria do conjunto de núcleos do cliente; workers/shard = workers de
# cada cliente. Pinning das réplicas, metodologia (60 s, aquecimento 10 s, 3 runs em ordem aleatória com semente fixa,
# refazimento dos pontos fora da curva) e medição (txtiming, marca D) iguais ao teste110_pin.sh.
# No servidor, latsetter_wan.py = resultados/scripts/latsetter.py do commit desta bateria (o latsetter.py das
# baterias antigas fica intacto lá; as duas cópias diferiam em 1 linha).
# Uso: COND=wan|controle DEST=2 CROSS=0.1 SUFIXO=_w teste110w.sh <puro|cft|bft> <N shards>
#   (env: WORKERS_LIST, NRUNS, DURATION, MAX_REFAZ, ORDERER, MATRIZ, CLI_BIN, PAR)
# PAR=canalA-canalB (12/11/2026, CDF por par): as tx globais vão só entre esses dois canais e são geradas só pelos
# clientes deles, com o dobro de CROSS (cada um manda para casa + o outro do par); os demais clientes só mandam tx
# locais. No total continuam CROSS de tx globais. Exige DEST=2 e o cliente com -parceiros.
# PAR_FATOR=f: os 2 clientes do par usam f × workers (e os núcleos correspondentes), para compensar a espera do RTT
# de WAN e igualar a vazão dos 4 clientes (sem isso a fatia de tx globais cai pela metade); f vem da calibração do
# rodar_wan_cdf.sh.
# DIAG=1 (12/11/2026, diagnóstico dos travamentos sob carga): réplicas com SKEEN_ALG3_STATS=1 (tx pendentes por fase a
# cada 2 s) e log de nível info do consenter; os logs das réplicas e os erros dos clientes de cada run vão para
# resultados_d/logs_<TAG>_run<run>_<rodada>/. Não usar em bateria de medição.
# RATE=r (13/11/2026): teto de r tx/s em cada cliente (-rate do cliente; exige cliente com -rate), para igualar a carga
# dos canais; WORKERS_LIST passa a ser só o teto de tx simultâneas.
set -e
export LC_NUMERIC=C
MODE=${1:?uso: $0 <puro|cft|bft> <N shards>}
NSHARDS=${2:?uso: $0 <puro|cft|bft> <N shards>}
BASEDIR=$(cd "$(dirname "$0")" && pwd)
FABRIC_DIR="$BASEDIR/../fabric"
BIN="$FABRIC_DIR/build/bin/${ORDERER:-orderer_alg3k}"
CLI_BIN=${CLI_BIN:-./cliente_e2e_wan_bin}
[ -z "${PAR:-}" ] || [ "$DEST" = 2 ] || { echo "PAR exige DEST=2"; exit 1; }
cd "$BASEDIR"

RC=6
case $MODE in
    cft) NREP=3; PROFILE=SkeenCFTRealGenesis ;;
    bft) NREP=4; PROFILE=SkeenBFTRealGenesis ;;
    puro) NREP=1; PROFILE=SkeenGenesis ;;
    *) echo "modo inválido: $MODE (só puro|cft|bft: os nativos não são fragmentados)"; exit 1 ;;
esac
COND=${COND:-wan}; case $COND in wan|controle) ;; *) echo "COND=$COND inválida"; exit 1 ;; esac
MATRIZ=${MATRIZ:-lat_ec2.csv}
DURATION=${DURATION:-60}
NRUNS=${NRUNS:-3}
WARMUP=10
CROSS=${CROSS:-0.1}
DEST=${DEST:-2}
[ "$DEST" -ge 2 ] && [ "$DEST" -le "$NSHARDS" ] || { echo "DEST=$DEST inválido para $NSHARDS shards"; exit 1; }
WORKERS_LIST=${WORKERS_LIST:-"25 100 300 600 1000 2000 4000"}
MAX_REFAZ=${MAX_REFAZ:-2}
BOOT_WAIT=$(( 25 + 5 * NSHARDS )); [ "$BOOT_WAIT" -lt 35 ] && BOOT_WAIT=35
[ "$MODE" = puro ] && BOOT_WAIT=10
TAG=g110w_${COND}_c$(echo $CROSS | tr -d .)_d${DEST}_${MODE}${NSHARDS}${SUFIXO:-}
SHM=/dev/shm/$TAG
DATA="$BASEDIR/ledger_$TAG"
DISCO="$BASEDIR/resultados_d/${TAG}_$(date +%d%m_%H%M).txt"; mkdir -p "$BASEDIR/resultados_d"
TT=${TTDIR:-$BASEDIR/resultados_d/tt/$TAG}
CRYPTO="$BASEDIR/crypto-config-wan"
mkdir -p "$SHM/cfg" "$TT"; rm -f "$TT"/txtiming_*
RES="$SHM/resultado.txt"
ip_of() { echo "127.0.$1.1"; }
HOSTS=$(for s in $(seq 1 $NSHARDS); do ip_of $s; done | paste -sd,)

gidx() { echo $(( ($1-1)*NREP + ($2-1) )); }
NODE0_REPL=$(( 72 / RC ))
TOTAL_REPL=$(( NSHARDS * NREP ))
SPILL=$(( TOTAL_REPL > NODE0_REPL ? TOTAL_REPL - NODE0_REPL : 0 ))
replica_cores() { local g=$(gidx $1 $2); if [ "$g" -lt "$NODE0_REPL" ]; then echo "$((g*RC))-$((g*RC + RC - 1))"; else local o=$(( g - NODE0_REPL )); echo "$((80 + o*RC))-$((80 + o*RC + RC - 1))"; fi; }
NODE1_BASE=$(( 80 + SPILL * RC ))
POOL_CLIENTE=$(seq -s ' ' $NODE1_BASE 159)
[ $(( 160 - NODE1_BASE )) -lt 16 ] && POOL_CLIENTE="$POOL_CLIENTE $(seq -s ' ' 72 78)"
CORE_MONITOR=79
FATIA=$(( $(echo $POOL_CLIENTE | wc -w) / NSHARDS ))   # núcleos por cliente (um cliente por local)
[ "$FATIA" -ge 1 ] || { echo "núcleos insuficientes para $NSHARDS clientes"; exit 1; }
client_cores_n() { local n=$(( ($1 + 24) / 25 )); [ "$n" -gt "$FATIA" ] && n=$FATIA; echo $n; }
client_cores_span() {  # $1 = local (1..N), $2 = workers desse cliente: os primeiros núcleos da fatia do local
    echo $POOL_CLIENTE | tr ' ' '\n' | tail -n +$(( ($1-1)*FATIA + 1 )) | head -n "$(client_cores_n "$2")" | paste -sd,
}
plano_pinning() {
    local s r l=""
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do l="$l s${s}r${r}=$(replica_cores $s $r)"; done; done
    echo "PINNING réplicas:$l | clientes: $NSHARDS × até $FATIA núcleos de {$(echo $POOL_CLIENTE | tr ' ' ',')} | monitor: $CORE_MONITOR"
}
gport() { echo $(( 7050 + ($1-1)*1000 + ($2-1) )); }

prepara_config() {
    [ -d "$CRYPTO" ] || "$FABRIC_DIR/build/bin/cryptogen" generate --config=crypto-config-wan.yaml --output="$CRYPTO" >/dev/null
    python3 gera_configtx_wan.py configtx.yaml "$CRYPTO" '127.0.{s}.1' > "$SHM/cfg/configtx.yaml"
    [ -z "${BATCH_TIMEOUT:-}" ] || sed -i "s/^    BatchTimeout: 2s\$/    BatchTimeout: ${BATCH_TIMEOUT}/" "$SHM/cfg/configtx.yaml"  # 16/11/2026: lote do Raft (CFT) menor
    cp orderer.yaml "$SHM/cfg/"
}

apply_latency_wan() {
    python3 latsetter_wan.py unset lo &>/dev/null || true
    python3 wan_zonas.py "$MATRIZ" "$NSHARDS" "$SHM/ips.csv" "$SHM/lats.csv" $([ "$COND" = controle ] && echo --controle)
    { echo "IP,Port,Latency"
      for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
          g=$(gport $s $r); h=$(ip_of $s); echo "$h,$g,2"; echo "$h,$((g+10000)),2"; echo "$h,$((g+20000)),2"
      done; done; } > "$SHM/lan.csv"
    python3 latsetter_wan.py set-lo-zones "$SHM/ips.csv" "$SHM/lats.csv" --lan "$SHM/lan.csv" --jitter-pct 0 --limit 200000 > "$SHM/lat.log" 2>&1
    grep -q "Error" "$SHM/lat.log" && { reg "LAT_ERRO $(grep -m1 Error "$SHM/lat.log")"; exit 1; }
    local s m="" rtt
    for s in $(seq 2 $NSHARDS); do
        rtt=$(python3 wan_rtt.py "$(ip_of 1)" "$(ip_of $s)" 3) || true
        [[ "$rtt" =~ ^[0-9.]+$ ]] || { reg "LAT_ERRO sem RTT de canal1 a canal$s (lo desligado?)"; exit 1; }
        m="$m canal1->canal$s=${rtt}ms"
    done
    reg "RTT medido ($COND, $MATRIZ):$m"
}

make_start_script() {
    local s=$1 r=$2
    local nd; [ "$r" -eq 1 ] && nd="orderer" || nd="orderer${r}"
    local td="$CRYPTO/ordererOrganizations/example.com/orderers/${nd}.example.com/tls"
    local md="$CRYPTO/ordererOrganizations/example.com/orderers/${nd}.example.com/msp"
    local k=$(gidx $s $r)
    mkdir -p "$DATA/s${s}r${r}/wal" "$DATA/s${s}r${r}/snapshot"
    {
        echo "#!/bin/bash"
        echo "export GOMAXPROCS=$RC SKEEN_NUM_SHARDS=$NSHARDS SKEEN_HOSTS=$HOSTS"
        echo "export FABRIC_CFG_PATH='$SHM/cfg'"
        echo "export ORDERER_GENERAL_LISTENADDRESS=$(ip_of $s) ORDERER_GENERAL_LISTENPORT=$(gport $s $r)"
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
        if [ -n "${DIAG:-}" ]; then
            echo "export SKEEN_ALG3_STATS=1 FABRIC_LOGGING_SPEC='error:orderer.consensus.skeen=info:SKEEN-BFT=info:orderer.consensus.etcdraft=info'"
        else
            echo "export FABRIC_LOGGING_SPEC=fatal"
        fi
        echo "exec $BIN"
    } > "$SHM/s${s}r${r}.sh"
    chmod +x "$SHM/s${s}r${r}.sh"
}

declare -A PIDS
logdir() {  # logs das réplicas: /dev/shm (64 MB) normalmente; no DIAG, disco (log de nível info)
    if [ -n "${DIAG:-}" ]; then mkdir -p "$BASEDIR/resultados_d/logs_${TAG}_atual"; echo "$BASEDIR/resultados_d/logs_${TAG}_atual"; else echo "$SHM"; fi
}
osn() {  # $1 = subcomando, $2 = canal, $3 = réplica, resto = argumentos
    local cmd=$1 s=$2 r=$3; shift 3
    local nd; [ "$r" -eq 1 ] && nd="orderer" || nd="orderer${r}"
    local td="$CRYPTO/ordererOrganizations/example.com/orderers/${nd}.example.com/tls"
    "$FABRIC_DIR/build/bin/osnadmin" channel $cmd --channelID "canal${s}" -o "127.0.0.1:$(( 9443 + $(gidx $s $r)*10 ))" \
        --ca-file "$td/ca.crt" --client-cert "$td/server.crt" --client-key "$td/server.key" "$@"
}
init_shards() {
    pkill -9 -f '[b]uild/bin/orderer' 2>/dev/null || true
    PIDS=()
    sleep 8
    rm -rf "$DATA" && mkdir -p "$DATA"
    export FABRIC_CFG_PATH="$SHM/cfg"
    for s in $(seq 1 $NSHARDS); do
        local prof="$PROFILE$s"; [ "$MODE" = puro ] && prof=$PROFILE
        "$FABRIC_DIR/build/bin/configtxgen" -profile "$prof" -channelID "canal${s}" -outputBlock "$SHM/canal${s}.block" &>/dev/null
    done
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
        make_start_script "$s" "$r"
        taskset -c "$(replica_cores $s $r)" bash "$SHM/s${s}r${r}.sh" &>"$(logdir)/s${s}r${r}.log" &
        PIDS[$s,$r]=$!   # taskset → bash → exec orderer: o mesmo pid vira o orderer
    done; done
    local i ok
    for i in $(seq 1 $BOOT_WAIT); do
        ok=1
        for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
            (echo > /dev/tcp/127.0.0.1/$(( 9443 + $(gidx $s $r)*10 ))) 2>/dev/null || ok=0
        done; done
        [ $ok = 1 ] && break; sleep 1
    done
    sleep 1
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
        osn join $s $r --config-block "$SHM/canal${s}.block" &>/dev/null || echo "WARN: join shard${s} r${r} falhou" >> "$RES"
    done; done
    if [ "$MODE" = puro ]; then sleep 3; else
        for i in $(seq 1 $BOOT_WAIT); do
            ok=1
            for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
                osn list $s $r 2>/dev/null | grep -q '"active"' || ok=0
            done; done
            [ $ok = 1 ] && break; sleep 1
        done
        case $MODE in cft) sleep 15;; *) sleep 10;; esac  # piso: eleição do líder (etcdraft) / visão inicial (SmartBFT)
    fi
    : > "$SHM/cfg.txt"
    for s in $(seq 1 $NSHARDS); do
        local h=$(ip_of $s)
        case $MODE in
            cft) echo "canal${s}=$h:$(gport $s 1),$h:$(gport $s 2),$h:$(gport $s 3)" >> "$SHM/cfg.txt" ;;
            *) echo "canal${s}=$h:$(gport $s 1)" >> "$SHM/cfg.txt" ;; # puro; BFT: líder r1
        esac
    done
    mkdir -p "$SHM/cli"; ln -sfn "$CRYPTO" "$SHM/cli/crypto-config"
}

resumir() {  # saídas dos N clientes concatenadas: soma de TPS e erros, TPS de cada local
    python3 -c "
import sys, re
t = sys.stdin.read()
tps = [float(x) for x in re.findall(r'TPS global\s*:\s*([\d.]+)', t)]
err = [int(x) for x in re.findall(r'Txs erro\s*:\s*(\d+)', t)]
print('TPS_total=%.2f erros=%d tps_por_local=%s' % (sum(tps), sum(err), '/'.join('%.0f' % x for x in tps)))
"
}

REPS=""; case $MODE in cft) REPS="-cft-replicas 3 -probe-leader -probe-count 5";; esac
reg() { echo "$1" >> "$RES"; echo "$1" >> "$DISCO"; }
roda_par() {  # $1 = workers por shard (= por cliente), $2 = run, $3 = rodada de refazimento (0 = original)
    local wps=$1 run=$2 refeita=$3
    init_shards
    local W=$((wps * NSHARDS)) nc=$(client_cores_n "$wps") T0=$(date +%s%N) s plano="$SHM/pin.txt" todos=""
    local -a CP=()
    : > "$plano"; rm -f "$SHM"/c*.pid
    for s in $(seq 1 $NSHARDS); do
        local wc=$wps cx=$CROSS extra=""
        if [ -n "${PAR:-}" ]; then
            case "-$PAR-" in
                *"-canal$s-"*) cx=$(awk -v c="$CROSS" 'BEGIN{print 2*c}'); extra="-parceiros $(echo "$PAR" | tr '-' '\n' | grep -vx "canal$s")"
                               wc=$(awk -v w="$wps" -v f="${PAR_FATOR:-1}" 'BEGIN{printf "%d", w*f + 0.5}') ;;
                *) cx=0 ;;
            esac
        fi
        local span=$(client_cores_span $s $wc) errf=/dev/null
        [ -z "${DIAG:-}" ] || errf="$SHM/cli$s.err"
        ( cd "$SHM/cli" && SKEEN_TXTIMING_DIR_CLIENTE="$TT" SKEEN_TXTIMING_SAMPLE=32 GOMAXPROCS=$(client_cores_n "$wc") exec taskset -c "$span" "$BASEDIR/$CLI_BIN" \
            -home canal$s $extra ${RATE:+-rate $RATE} -workers "$wc" -cross "$cx" -shards "$DEST" -payload 128 -config "$SHM/cfg.txt" -out-sum "" \
            -duration "$DURATION" -warmup "$WARMUP" -rpc-timeout 30 $REPS ) > "$SHM/cli$s.out" 2>"$errf" &
        CP+=($!); echo $! > "$SHM/c${s}.pid"; echo "cliente$s $! $span" >> "$plano"
    done
    sleep $WARMUP
    for s in $(seq 1 $NSHARDS); do for r in $(seq 1 $NREP); do
        echo "s${s}r${r} ${PIDS[$s,$r]} $(replica_cores $s $r)" >> "$plano"; todos="$todos ${PIDS[$s,$r]}"
        echo "${PIDS[$s,$r]}" > "$SHM/s${s}r${r}.pid"
    done; done
    local pin; pin=$(taskset -c $CORE_MONITOR python3 pin_cpu.py confere "$plano") || {
        reg "PIN_ERRO modo=$MODE workers_por_shard=$wps run=$run $pin"; kill -9 "${CP[@]}" 2>/dev/null; exit 1; }
    local ta=$(date +%s.%N) a=$(taskset -c $CORE_MONITOR python3 pin_cpu.py ticks "${CP[@]}" $todos)
    sleep $(( (DURATION - WARMUP) / 2 ))
    ss -tnpH state established > "$SHM/conexoes.txt"
    local wan; wan=$(taskset -c $CORE_MONITOR python3 wan_confere_conexoes.py "$SHM/conexoes.txt" "$SHM") || {
        reg "WAN_ERRO modo=$MODE workers_por_shard=$wps run=$run $(echo "$wan" | tr '\n' ';')"; kill -9 "${CP[@]}" 2>/dev/null; exit 1; }
    wan=$(echo "$wan" | head -1)
    sleep $(( DURATION - WARMUP - (DURATION - WARMUP) / 2 - 4 ))
    local tb=$(date +%s.%N) b=$(taskset -c $CORE_MONITOR python3 pin_cpu.py ticks "${CP[@]}" $todos)
    local cpu=$(taskset -c $CORE_MONITOR python3 pin_cpu.py uso "$(awk -v a=$ta -v b=$tb 'BEGIN{print b-a}')" "$a" "$b" $nc $RC $NSHARDS)
    wait "${CP[@]}" || true
    local out=$(cat "$SHM"/cli*.out | resumir)
    sleep 5
    pkill -USR1 -f '[b]uild/bin/orderer' 2>/dev/null || true
    sleep 3
    local tt=$(SKEEN_TXTIMING_SAMPLE=32 python3 txtiming_agrega_d.py "$TT" "$T0" "$WARMUP" "$DURATION" --apaga 2>/dev/null)
    local conex=$(echo "$wan" | grep -o "certa: [0-9]*" | tr -d ' ')
    if [ -n "${DIAG:-}" ]; then
        local LD="$BASEDIR/resultados_d/logs_${TAG}_run${run}_${refeita}"; mkdir -p "$LD"
        mv "$(logdir)"/s*r*.log "$LD/" 2>/dev/null || true; cp "$SHM"/cli*.err "$SHM"/cli*.out "$LD/" 2>/dev/null || true
    fi
    reg "modo=$MODE cross=$CROSS workers_por_shard=$wps run=$run refeita=$refeita shards=$NSHARDS dest=$DEST total=$W cond=$COND${PAR:+ par=$PAR par_fator=${PAR_FATOR:-1}}${RATE:+ rate=$RATE} cores_cliente=$nc  $out  $tt  $pin  $cpu  conexoes_entre_locais_$conex  hora=$(date +%T)"
}
prepara_config
apply_latency_wan
R0=0
if [ -n "${PARES:-}" ]; then
    R0=${RODADA0:-3}
    reg "REFAZ_FINAL $(date '+%d/%m %T') modo=$MODE ${NSHARDS} shards dest=$DEST cond=$COND pares={$PARES}"
    for par in $PARES; do roda_par "${par%%:*}" "${par##*:}" "$R0"; done
else
    reg "INICIO $(date '+%d/%m %T') WAN cond=$COND matriz=$MATRIZ modo=$MODE ${NSHARDS} shards (um por local), CROSS=$CROSS (cada tx cross em $DEST shards: o de casa + sorteados)${PAR:+, PAR=$PAR (tx globais só entre esse par)}, $(basename $BIN) md5=$(md5sum $BIN | cut -c1-12) cliente md5=$(md5sum $CLI_BIN | cut -c1-12), ${DURATION}s (warmup ${WARMUP}s), ${NRUNS} runs, workers/shard={$WORKERS_LIST}, LAN 2ms dentro do local, MAX_REFAZ=$MAX_REFAZ"
    PARES=$(for w in $WORKERS_LIST; do for r in $(seq 1 $NRUNS); do echo "$w:$r"; done; done | shuf --random-source=<(yes 20261105))
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
python3 latsetter_wan.py unset lo &>/dev/null || true
pkill -9 -f '[b]uild/bin/orderer' 2>/dev/null || true
rm -rf "$DATA"
reg "FIM_G110W $(date '+%d/%m %T')"
