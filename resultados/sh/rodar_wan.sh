#!/bin/bash
# Bateria de WAN (05/11/2026): um canal por local (4 canais = sa-east-1, us-east-1, eu-central-1, ap-northeast-1, matriz
# lat_ec2.csv), 2 destinos por tx cross, Skeen-puro, Skeen-CFT e Skeen-BFT, cada curva em duas condições na mesma
# topologia: controle (RTT 2 ms entre locais, = LAN das baterias) e wan (matriz de regiões). A comparação é na mesma
# vazão (curvas latência × vazão), não no mesmo nº de workers: na WAN o cliente em laço fechado espera o RTT de WAN
# por tx cross e precisa de mais workers para a mesma carga (por isso os níveis vão até 4000 por canal).
#   0. Coordenador único (teste109x_diag.sh, alg3k, CFT e BFT): 1 réplica com coord=true por canal, senão ABORTA.
#   0b. A/B pareado alg3i × alg3k sem WAN (teste110_pin.sh, CFT e BFT, 4 shards, cross 0,1, 2 destinos, 200 clientes/
#      shard, alternados): o alg3k sem SKEEN_HOSTS tem de ser o alg3i; diferença > 5% = ABORTA.
#   1. Para cada cross em $CROSS_LIST: puro, cft, bft; controle e wan alternados (o mesmo regime do servidor afeta os dois).
# Log: /dev/shm/seq_wan.txt e resultados_d/seq_wan.txt. Uso: CROSS_LIST="0.1 0.2" nohup bash rodar_wan.sh &
cd "$(dirname "$0")"
P=/dev/shm/seq_wan.txt; PD=resultados_d/seq_wan.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
B=../fabric/build/bin
for f in $B/orderer_alg3k $B/orderer_alg3i $B/cryptogen ./cliente_e2e_wan_bin ./cliente_e2e_bin; do
    [ -x $f ] || { log "ERRO falta $f"; exit 1; }
done
CROSS_LIST=${CROSS_LIST:-0.1}
log "WAN_INICIO alg3k md5=$(md5sum $B/orderer_alg3k | cut -c1-12) cliente_wan md5=$(md5sum cliente_e2e_wan_bin | cut -c1-12) teste110w=$(md5sum teste110w.sh | cut -c1-12) matriz=$(md5sum lat_ec2.csv | cut -c1-12) cross={$CROSS_LIST}"
niveis() { [ "$1" = bft ] && echo "25 100 300 600 1000 2000" || echo "25 100 300 600 1000 2000 4000"; }
espaco() { local livre=$(df -k /dev/shm | awk 'NR==2{print $4}'); [ "$livre" -ge 20000 ] || { log "ABORTA /dev/shm com ${livre} KB livres"; exit 1; }; }
guarda() {  # $1 = início (epoch) da curva; $2 = rótulo
    local dur=$(( $(date +%s) - $1 ))
    [ $dur -ge 300 ] || { log "ABORTA curva $2 durou ${dur}s (< 5 min): ver /dev/shm e logs"; exit 1; }
}
curva() {  # $1 modo, $2 fração, $3 condição
    local d=/dev/shm/g110w_${3}_c$(echo $2 | tr -d .)_d2_${1}4_w
    rm -rf $d; espaco; local t0=$(date +%s)
    log "inicio $1 4 canais cross=$2 dest=2 cond=$3"
    COND=$3 CROSS=$2 DEST=2 SUFIXO=_w NRUNS=3 DURATION=60 MAX_REFAZ=2 WORKERS_LIST="$(niveis $1)" bash teste110w.sh $1 4 > /dev/null 2>&1
    local e; e=$(grep -m1 -E "^(PIN_ERRO|WAN_ERRO|LAT_ERRO)" $d/resultado.txt 2>/dev/null) && { log "ABORTA $1 cross=$2 cond=$3: $e"; exit 1; }
    log "fim $1 4 canais cross=$2 dest=2 cond=$3 ($(grep -c '^modo=' $d/resultado.txt 2>/dev/null) runs)"; guarda $t0 "$1 cross=$2 cond=$3"
}
ab() {  # $1 modo, $2 binário, $3 repetição
    local D=/dev/shm/g109dx_c01_d2_${1}4_ab${2##*_}$3; rm -rf $D; espaco
    ORDERER=$2 CROSS=0.1 DEST=2 SUFIXO=_ab${2##*_}$3 NRUNS=1 DURATION=60 MAX_REFAZ=0 WORKERS_LIST="200" bash teste110_pin.sh $1 4 > /dev/null 2>&1
    grep -q "^PIN_ERRO" $D/resultado.txt 2>/dev/null && { log "ABORTA PIN_ERRO no A/B $1 $2: $(grep '^PIN_ERRO' $D/resultado.txt | head -1)"; exit 1; }
    grep -o 'd_tps=[0-9]*' $D/resultado.txt | tail -1 | cut -d= -f2
}
coord() {  # $1 modo: run curto com estatísticas; exatamente 1 réplica com coord=true por canal (validar-execucao §2.2)
    local D=g109dx_c01_d2_${1}4_coordk s n l=""
    ORDERER=orderer_alg3k CROSS=0.1 DEST=2 SUFIXO=_coordk NRUNS=1 DURATION=30 MAX_REFAZ=0 WORKERS_LIST="100" bash teste109x_diag.sh $1 4 > /dev/null 2>&1
    for s in 1 2 3 4; do
        n=$(for f in resultados_d/logs_$D/s${s}r*.log; do grep -h 'STATS\]' $f | tail -1; done | grep -c 'coord=true')
        l="$l canal$s=$n"; [ "$n" = 1 ] || { log "ABORTA coordenador $1 canal$s: $n réplicas com coord=true"; exit 1; }
    done
    log "COORD $1 alg3k 4 shards: coord=true por canal:$l"
}
if [ -z "$SEM_AB" ]; then
    coord cft; coord bft
    for m in cft bft; do
        i1=$(ab $m orderer_alg3i 1); k1=$(ab $m orderer_alg3k 1); k2=$(ab $m orderer_alg3k 2); i2=$(ab $m orderer_alg3i 2)
        dif=$(awk -v a="${k1:-0}" -v b="${k2:-0}" -v c="${i1:-0}" -v d="${i2:-0}" 'BEGIN{ if (c+d>0) printf "%.1f", 100*((a+b)-(c+d))/(c+d); else print "NA" }')
        log "AB $m 4 shards cross=0.1 dest=2 200 cli: alg3i=${i1:-NA},${i2:-NA} alg3k=${k1:-NA},${k2:-NA} diferenca_alg3k=${dif}%"
        awk -v d="$dif" 'BEGIN{ exit !(d=="NA" || d>5 || d<-5) }' && { log "ABORTA A/B $m: alg3k difere do alg3i em mais de 5%"; exit 1; }
    done
    log "FIM_AB"
fi
for c in $CROSS_LIST; do
    for m in puro cft bft; do for cond in controle wan; do curva $m $c $cond; done; done
    log "FIM_CROSS_$c"
done
log "FIM_WAN"
