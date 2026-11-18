#!/bin/bash
# Bateria curta de WAN por par de destinos (12/11/2026, gráfico 39): a mesma topologia e o mesmo binário do rodar_wan.sh,
# só no nível abaixo da saturação da tabela 4 (Skeen-puro e Skeen-CFT 300 clientes/canal, Skeen-BFT 100), 10% cross,
# 2 destinos, 3 runs, controle e WAN alternados. O txtiming_agrega_d.py passou a imprimir pares=<destinos>:avg:p50:n
# (latência S→D das tx cross por par de canais), o que a bateria de 10–11/11 não guardava.
# Log: /dev/shm/seq_wan_pares.txt e resultados_d/seq_wan_pares.txt. Uso: nohup bash rodar_wan_pares.sh &
cd "$(dirname "$0")"
P=/dev/shm/seq_wan_pares.txt; PD=resultados_d/seq_wan_pares.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
B=../fabric/build/bin
for f in $B/orderer_alg3k ./cliente_e2e_wan_bin; do [ -x $f ] || { log "ERRO falta $f"; exit 1; }; done
log "WAN_PARES_INICIO alg3k md5=$(md5sum $B/orderer_alg3k | cut -c1-12) cliente_wan md5=$(md5sum cliente_e2e_wan_bin | cut -c1-12) teste110w=$(md5sum teste110w.sh | cut -c1-12) agrega=$(md5sum txtiming_agrega_d.py | cut -c1-12)"
for m in puro cft bft; do
    w=300; [ $m = bft ] && w=100
    for cond in controle wan; do
        d=/dev/shm/g110w_${cond}_c01_d2_${m}4_pares
        rm -rf $d
        log "inicio $m cond=$cond workers=$w"
        COND=$cond CROSS=0.1 DEST=2 SUFIXO=_pares NRUNS=3 DURATION=60 MAX_REFAZ=0 WORKERS_LIST="$w" bash teste110w.sh $m 4 > /dev/null 2>&1
        e=$(grep -m1 -E "^(PIN_ERRO|WAN_ERRO|LAT_ERRO)" $d/resultado.txt 2>/dev/null) && { log "ABORTA $m cond=$cond: $e"; exit 1; }
        log "fim $m cond=$cond ($(grep -c '^modo=' $d/resultado.txt 2>/dev/null) runs, $(grep -c 'pares=' $d/resultado.txt 2>/dev/null) com pares)"
    done
done
log "FIM_WAN_PARES"
