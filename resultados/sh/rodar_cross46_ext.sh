#!/bin/bash
# Extensão das curvas COM CROSS do run de 08–10/10 (rodar_cross46.sh), de 10/10: mais níveis de carga
# além do último, para a curva latência × vazão mostrar a subida vertical. Mesma bateria (teste110_pin.sh: 6 núcleos em
# todas as réplicas), mesmo binário, pinning conferido.
#   Skeen-puro e Skeen-CFT (4 e 6 shards): 1000 (checagem, já medido) 2000 3000 4000 clientes/shard
#   Skeen-BFT (4 shards; 6 shards não cabe nos 160 núcleos): 500 (checagem) 600 800 1000
#   destinos 2 e 3; 5, 10 e 20% de tx globais. Sufixo _p3 (/dev/shm/g109dx_c<cross>_d<dest>_<modo><N>_p3).
# O nível de checagem confere que o servidor está no mesmo estado do run original (mesmo boot desde 09/10 11:36).
# Log: /dev/shm/seq_cross46_ext.txt e resultados_d/seq_cross46_ext.txt.
cd "$(dirname "$0")"
P=/dev/shm/seq_cross46_ext.txt; PD=resultados_d/seq_cross46_ext.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
export ORDERER=orderer_alg3i
B=../fabric/build/bin/orderer_alg3i
[ -x $B ] || { log "ERRO falta orderer_alg3i"; exit 1; }
log "CROSS46_EXT_INICIO orderer_alg3i md5=$(md5sum $B | cut -c1-12) teste110_pin=$(md5sum teste110_pin.sh | cut -c1-12)"
guarda() { local dur=$(( $(date +%s) - $1 )); [ $dur -lt 300 ] && { log "ABORTA curva $2 durou ${dur}s (< 5 min)"; exit 1; }; }
espaco() { local livre=$(df -k /dev/shm | awk 'NR==2{print $4}'); [ "$livre" -ge 20000 ] || { log "ABORTA /dev/shm com ${livre} KB livres"; exit 1; }; }
curva() {  # $1 modo, $2 shards, $3 fração, $4 destinos, $5 níveis
    local d=/dev/shm/g109dx_c$(echo $3 | tr -d .)_d${4}_${1}${2}_p3
    rm -rf $d; espaco; local t0=$(date +%s)
    log "inicio $1 $2 shards cross=$3 dest=$4 extensão {$5}"
    CROSS=$3 DEST=$4 SUFIXO=_p3 NRUNS=3 DURATION=60 MAX_REFAZ=2 WORKERS_LIST="$5" bash teste110_pin.sh $1 $2 > /dev/null 2>&1
    grep -q "^PIN_ERRO" $d/resultado.txt 2>/dev/null && { log "ABORTA PIN_ERRO em $1 $2 cross=$3 dest=$4: $(grep '^PIN_ERRO' $d/resultado.txt | head -1)"; exit 1; }
    log "fim $1 $2 shards cross=$3 dest=$4 extensão ($(grep -c '^modo=' $d/resultado.txt 2>/dev/null) runs)"; guarda $t0 "$1 $2 cross=$3 dest=$4"
}
EXT="1000 2000 3000 4000"; EXTB="500 600 800 1000"
for n in 4 6; do for dest in 2 3; do for c in 0.05 0.1 0.2; do
    for m in puro cft; do curva $m $n $c $dest "$EXT"; done
    [ $n = 4 ] && curva bft $n $c $dest "$EXTB"
done; done; done
python3 latsetter.py unset lo &>/dev/null || true
log "FIM_CROSS46_EXT"
