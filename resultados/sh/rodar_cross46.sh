#!/bin/bash
# Testes de cross-shard (08/10/2026): 4 e 6 shards; cada tx global com 2 ou 3 destinos
# sorteados entre os N shards; 5%, 10% e 20% de tx globais; Skeen-puro, Skeen-CFT e Skeen-BFT; pinning explícito
# conferido em todo run (teste110_pin.sh + pin_cpu.py). Binário orderer_alg3i = alg3h + canais até 6 (SKEEN_NUM_SHARDS).
#   0. A/B pareado alg3h × alg3i (CFT e BFT, 4 shards sem cross, 200 clientes/shard, 2 runs de cada, alternados);
#      diferença > 5% = ABORTA. 0b. linha de base dos 4 shards sem cross neste run
#   1. 4 shards: destinos 2 e 3 × 5/10/20%
#   2. 6 shards sem cross (linha de base dos 6 shards; a de 4 shards é a do run de 06/10)
#   3. 6 shards: destinos 2 e 3 × 5/10/20%
# 6 níveis por curva (25, 100, 200, 300, 600, 1000 clientes/shard; BFT 25–500), 3 runs de 60 s em ordem aleatória,
# refazimento de pontos fora da curva (até 2 rodadas), boot por prontidão. Sufixo _p nos diretórios
# (/dev/shm/g109dx_c<cross>_d<dest>_<modo><N>_p). Log: /dev/shm/seq_cross46.txt e resultados_d/seq_cross46.txt.
cd "$(dirname "$0")"
P=/dev/shm/seq_cross46.txt; PD=resultados_d/seq_cross46.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
export ORDERER=orderer_alg3i
B=../fabric/build/bin/orderer_alg3i
[ -x $B ] || { log "ERRO falta orderer_alg3i"; exit 1; }
log "CROSS46_INICIO orderer_alg3i md5=$(md5sum $B | cut -c1-12) configtx=$(md5sum configtx.yaml | cut -c1-12) teste110_pin=$(md5sum teste110_pin.sh | cut -c1-12)"
N6="25 100 200 300 600 1000"; N6B="25 100 200 300 400 500"
niveis() { [ "$1" = bft ] && echo "$N6B" || echo "$N6"; }
guarda() {  # $1 = início (epoch) da curva; $2 = rótulo
    local dur=$(( $(date +%s) - $1 ))
    if [ $dur -lt 300 ]; then log "ABORTA curva $2 durou ${dur}s (< 5 min): ver /dev/shm, PIN_ERRO e logs"; exit 1; fi
}
espaco() { local livre=$(df -k /dev/shm | awk 'NR==2{print $4}'); [ "$livre" -ge 20000 ] || { log "ABORTA /dev/shm com ${livre} KB livres"; exit 1; }; }
curva() {  # $1 modo, $2 shards, $3 fração, $4 destinos
    local d=/dev/shm/g109dx_c$(echo $3 | tr -d .)_d${4}_${1}${2}_p
    rm -rf $d; espaco; local t0=$(date +%s)
    log "inicio $1 $2 shards cross=$3 dest=$4"
    CROSS=$3 DEST=$4 SUFIXO=_p NRUNS=3 DURATION=60 MAX_REFAZ=2 WORKERS_LIST="$(niveis $1)" bash teste110_pin.sh $1 $2 > /dev/null 2>&1
    grep -q "^PIN_ERRO" $d/resultado.txt 2>/dev/null && { log "ABORTA PIN_ERRO em $1 $2 shards cross=$3 dest=$4: $(grep '^PIN_ERRO' $d/resultado.txt | head -1)"; exit 1; }
    log "fim $1 $2 shards cross=$3 dest=$4 ($(grep -c '^modo=' $d/resultado.txt 2>/dev/null) runs)"; guarda $t0 "$1 $2 cross=$3 dest=$4"
}
# 0. A/B pareado (08/10, 11h: a calibração contra o run de 06/10 deu +10,4% no CFT, acima de qualquer run daquele dia):
#    alg3h × alg3i alternados, mesma bateria e pinning, CFT e BFT, 4 shards sem cross, 200 clientes/shard, 2 runs de
#    cada. Diferença do binário > 5% = ABORTA. A linha de base dos 4 shards passa a ser medida NESTE run (passo 0b).
ab() {  # $1 modo, $2 binário, $3 repetição
    local D=/dev/shm/g109dx_c0_d2_${1}4_ab${2##*_}$3; rm -rf $D; espaco
    ORDERER=$2 CROSS=0 DEST=2 SUFIXO=_ab${2##*_}$3 NRUNS=1 DURATION=60 MAX_REFAZ=0 WORKERS_LIST="200" bash teste110_pin.sh $1 4 > /dev/null 2>&1
    grep -q "^PIN_ERRO" $D/resultado.txt 2>/dev/null && { log "ABORTA PIN_ERRO no A/B $1 $2: $(grep '^PIN_ERRO' $D/resultado.txt | head -1)"; exit 1; }
    grep -o 'd_tps=[0-9]*' $D/resultado.txt | tail -1 | cut -d= -f2
}
for m in cft bft; do
    h1=$(ab $m orderer_alg3h 1); i1=$(ab $m orderer_alg3i 1); i2=$(ab $m orderer_alg3i 2); h2=$(ab $m orderer_alg3h 2)
    dif=$(awk -v a="${i1:-0}" -v b="${i2:-0}" -v c="${h1:-0}" -v d="${h2:-0}" 'BEGIN{ if (c+d>0) printf "%.1f", 100*((a+b)-(c+d))/(c+d); else print "NA" }')
    log "AB $m 4 shards sem cross 200 cli: alg3h=${h1:-NA},${h2:-NA} alg3i=${i1:-NA},${i2:-NA} diferenca_alg3i=${dif}% (ref. 06/10 alg3h: cft 33349, bft 19425)"
    awk -v d="$dif" 'BEGIN{ exit !(d=="NA" || d>5 || d<-5) }' && { log "ABORTA A/B $m: alg3i difere do alg3h em mais de 5%"; exit 1; }
done
# 0b. linha de base dos 4 shards sem cross, medida neste run
for m in puro cft bft; do curva $m 4 0 2; done
log "FIM_4SHARDS_SEM_CROSS"
# 1. 4 shards
for dest in 2 3; do for c in 0.05 0.1 0.2; do for m in puro cft bft; do curva $m 4 $c $dest; done; done; done
log "FIM_4SHARDS"
# 2. 6 shards sem cross
for m in puro cft bft; do curva $m 6 0 2; done
log "FIM_6SHARDS_SEM_CROSS"
# 3. 6 shards
for dest in 2 3; do for c in 0.05 0.1 0.2; do for m in puro cft bft; do curva $m 6 $c $dest; done; done; done
python3 latsetter.py unset lo &>/dev/null || true
log "FIM_CROSS46"
