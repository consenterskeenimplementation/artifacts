#!/bin/bash
# Bateria SEM CROSS com o servidor no estado mais rápido: Skeen-puro, Raft nativo,
# SmartBFT nativo, Skeen-CFT e Skeen-BFT com 1 shard; os Skeens também com 4 e 6 shards. Mesmo binário para todos
# (orderer_alg3i), pinning conferido em todo run (teste111_semcross.sh + pin_cpu.py).
#   0. A/B do layout do BFT: Skeen-BFT, 4 shards, 300 clientes/shard, seguidoras com 6 × 4 núcleos, alternados, 2 runs de
#      cada; diferença > 5% = ABORTA (o layout novo não pode mudar o resultado onde o cliente já tinha folga)
#   1. 1 shard: Raft, SmartBFT, Skeen-puro, Skeen-CFT, Skeen-BFT
#   2. 4 shards: Skeen-puro, Skeen-CFT, Skeen-BFT
#   3. 6 shards: Skeen-puro, Skeen-CFT, Skeen-BFT
# 11 níveis por curva (25, 50, 100, 150, 200, 250, 300, 350, 400, 600, 1000 clientes/shard; BFT e SmartBFT 25–500), 3 runs de
# 60 s em ordem aleatória, refazimento de pontos fora da curva (até 2 rodadas), boot por prontidão. Diretórios
# /dev/shm/g109dx_c0_d1_<modo><N>_s. Log: /dev/shm/seq_semcross.txt e resultados_d/seq_semcross.txt.
cd "$(dirname "$0")"
P=/dev/shm/seq_semcross.txt; PD=resultados_d/seq_semcross.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
export ORDERER=orderer_alg3i
B=../fabric/build/bin/orderer_alg3i
[ -x $B ] || { log "ERRO falta orderer_alg3i"; exit 1; }
log "SEMCROSS_INICIO orderer_alg3i md5=$(md5sum $B | cut -c1-12) configtx=$(md5sum configtx.yaml | cut -c1-12) teste111=$(md5sum teste111_semcross.sh | cut -c1-12)"
N11="25 50 100 150 200 250 300 350 400 600 1000"; N11B="25 50 100 150 200 250 300 350 400 500"
niveis() { case $1 in bft|bftsmart) echo "$N11B";; *) echo "$N11";; esac; }
guarda() {  # $1 = início (epoch) da curva; $2 = rótulo
    local dur=$(( $(date +%s) - $1 ))
    if [ $dur -lt 300 ]; then log "ABORTA curva $2 durou ${dur}s (< 5 min): ver /dev/shm, PIN_ERRO e logs"; exit 1; fi
}
espaco() { local livre=$(df -k /dev/shm | awk 'NR==2{print $4}'); [ "$livre" -ge 20000 ] || { log "ABORTA /dev/shm com ${livre} KB livres"; exit 1; }; }
curva() {  # $1 modo, $2 shards
    local d=/dev/shm/g109dx_c0_d1_${1}${2}_s
    rm -rf $d; espaco; local t0=$(date +%s)
    log "inicio $1 $2 shard(s) sem cross"
    SUFIXO=_s NRUNS=3 DURATION=60 MAX_REFAZ=2 WORKERS_LIST="$(niveis $1)" bash teste111_semcross.sh $1 $2 > /dev/null 2>&1
    grep -q "^PIN_ERRO" $d/resultado.txt 2>/dev/null && { log "ABORTA PIN_ERRO em $1 $2: $(grep '^PIN_ERRO' $d/resultado.txt | head -1)"; exit 1; }
    log "fim $1 $2 shard(s) sem cross ($(grep -c '^modo=' $d/resultado.txt 2>/dev/null) runs)"; guarda $t0 "$1 $2 sem cross"
}
# 0. A/B do layout do BFT
lay() {  # $1 núcleos das seguidoras, $2 repetição
    local D=/dev/shm/g109dx_c0_d1_bft4_lay$1_$2; rm -rf $D; espaco
    BFT_RC_SEGUIDORA=$1 SUFIXO=_lay$1_$2 NRUNS=1 DURATION=60 MAX_REFAZ=0 WORKERS_LIST="300" bash teste111_semcross.sh bft 4 > /dev/null 2>&1
    grep -o 'd_tps=[0-9]*' $D/resultado.txt 2>/dev/null | tail -1 | cut -d= -f2
}
a1=$(lay 6 1); b1=$(lay 4 1); b2=$(lay 4 2); a2=$(lay 6 2)
dif=$(awk -v a="${a1:-0}" -v b="${a2:-0}" -v c="${b1:-0}" -v d="${b2:-0}" 'BEGIN{ if (a+b>0) printf "%.1f", 100*((c+d)-(a+b))/(a+b); else print "NA" }')
log "LAYOUT bft 4 shards 300 cli: seguidoras6=${a1:-NA},${a2:-NA} seguidoras4=${b1:-NA},${b2:-NA} diferenca=${dif}%"
awk -v d="$dif" 'BEGIN{ exit !(d=="NA" || d>5 || d<-5) }' && { log "ABORTA layout do BFT muda o resultado em mais de 5%"; exit 1; }
# 1–3
for m in raft bftsmart puro cft bft; do curva $m 1; done
log "FIM_1SHARD"
for n in 4 6; do for m in puro cft bft; do curva $m $n; done; log "FIM_${n}SHARDS"; done
python3 latsetter.py unset lo &>/dev/null || true
log "FIM_SEMCROSS"
