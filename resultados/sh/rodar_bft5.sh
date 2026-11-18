#!/bin/bash
# Série BFT com 5 núcleos por réplica, para medir o Skeen-BFT com 6 shards
# sem o cliente limitar o resultado. Mesmo binário (orderer_alg3i), pinning conferido em todo run (teste113_bft5.sh).
#   1. sem cross: SmartBFT nativo 1 shard; Skeen-BFT 1, 4 e 6 shards — 13 níveis (25 … 1000 clientes/shard)
#   2. com cross: Skeen-BFT 4 e 6 shards × 2 e 3 destinos × 5/10/20% — 9 níveis (25 … 1000)
# 3 runs de 60 s por nível em ordem aleatória (semente fixa), refazimento de pontos fora da curva, boot por prontidão.
# Sufixo _b5 (/dev/shm/g109dx_c<cross>_d<dest>_<modo><N>_b5). Log: /dev/shm/seq_bft5.txt e resultados_d/seq_bft5.txt.
cd "$(dirname "$0")"
P=/dev/shm/seq_bft5.txt; PD=resultados_d/seq_bft5.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
export ORDERER=orderer_alg3i BFT_RC=5
B=../fabric/build/bin/orderer_alg3i
[ -x $B ] || { log "ERRO falta orderer_alg3i"; exit 1; }
log "BFT5_INICIO orderer_alg3i md5=$(md5sum $B | cut -c1-12) teste113=$(md5sum teste113_bft5.sh | cut -c1-12) BFT_RC=5"
NSEM="25 50 100 150 200 250 300 350 400 500 600 800 1000"; NCROSS="25 100 200 300 400 500 600 800 1000"
guarda() { local dur=$(( $(date +%s) - $1 )); [ $dur -lt 300 ] && { log "ABORTA curva $2 durou ${dur}s (< 5 min)"; exit 1; }; }
espaco() { local livre=$(df -k /dev/shm | awk 'NR==2{print $4}'); [ "$livre" -ge 20000 ] || { log "ABORTA /dev/shm com ${livre} KB livres"; exit 1; }; }
curva() {  # $1 modo, $2 shards, $3 fração, $4 destinos, $5 níveis
    local d=/dev/shm/g109dx_c$(echo $3 | tr -d .)_d${4}_${1}${2}_b5
    rm -rf $d; espaco; local t0=$(date +%s)
    log "inicio $1 $2 shard(s) cross=$3 dest=$4"
    CROSS=$3 DEST=$4 SUFIXO=_b5 NRUNS=3 DURATION=60 MAX_REFAZ=2 WORKERS_LIST="$5" bash teste113_bft5.sh $1 $2 > /dev/null 2>&1
    grep -q "^PIN_ERRO" $d/resultado.txt 2>/dev/null && { log "ABORTA PIN_ERRO em $1 $2 cross=$3 dest=$4: $(grep '^PIN_ERRO' $d/resultado.txt | head -1)"; exit 1; }
    log "fim $1 $2 shard(s) cross=$3 dest=$4 ($(grep -c '^modo=' $d/resultado.txt 2>/dev/null) runs)"; guarda $t0 "$1 $2 cross=$3 dest=$4"
}
# 1. sem cross
curva bftsmart 1 0 1 "$NSEM"
for n in 1 4 6; do curva bft $n 0 1 "$NSEM"; done
log "FIM_BFT5_SEM_CROSS"
# 2. com cross
for n in 4 6; do for dest in 2 3; do for c in 0.05 0.1 0.2; do curva bft $n $c $dest "$NCROSS"; done; done; done
python3 latsetter.py unset lo &>/dev/null || true
log "FIM_BFT5"
