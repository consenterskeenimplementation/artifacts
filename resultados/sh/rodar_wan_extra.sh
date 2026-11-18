#!/bin/bash
# Níveis extras da bateria de WAN, 10% cross (13/11/2026, para o gráfico wan_lat_vazao_unido):
#   Skeen-BFT: 40, 50, 65 e 80 clientes/canal (a curva salta de 4,7 mil tx/s em 25 para 13,2 mil em 100);
#   Skeen-CFT: 1500 clientes/canal (ver se a volta depois do pico de 1000 é suave; 2000 e 4000 não se reproduziram).
# Mesma configuração da rodar_wan.sh (orderer_alg3k, cliente_e2e_wan_bin, WAN, 2 destinos, 3 runs de 60 s, MAX_REFAZ=2),
# em pastas próprias (sufixo _wx) para não tocar as da bateria principal (_w). Espera o rodar_wan_diag13.sh terminar.
# Log: /dev/shm/seq_wan_extra.txt e resultados_d/seq_wan_extra.txt; marcador FIM_WAN_EXTRA.
cd "$(dirname "$0")"
P=/dev/shm/seq_wan_extra.txt; PD=resultados_d/seq_wan_extra.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
for i in $(seq 1 240); do pgrep -f '[r]odar_wan_diag13' > /dev/null || break; sleep 30; done
pgrep -f '[r]odar_wan_diag13' > /dev/null && { log "ABORTA diag13 ainda rodando após 2 h"; exit 1; }
sleep 10
pgrep -f '[b]uild/bin/orderer' > /dev/null && { log "ABORTA orderer ainda rodando"; exit 1; }
B=../fabric/build/bin
for f in $B/orderer_alg3k ./cliente_e2e_wan_bin; do [ -x $f ] || { log "ERRO falta $f"; exit 1; }; done
log "WAN_EXTRA_INICIO alg3k md5=$(md5sum $B/orderer_alg3k | cut -c1-12) cliente_wan md5=$(md5sum cliente_e2e_wan_bin | cut -c1-12) teste110w=$(md5sum teste110w.sh | cut -c1-12)"
while read -r modo niveis; do
    [ -z "$modo" ] && continue
    d=/dev/shm/g110w_wan_c01_d2_${modo}4_wx; rm -rf $d
    log "inicio $modo wan cross=0.1 niveis={$niveis}"
    COND=wan CROSS=0.1 DEST=2 SUFIXO=_wx NRUNS=3 DURATION=60 MAX_REFAZ=2 WORKERS_LIST="$niveis" bash teste110w.sh $modo 4 > /dev/null 2>&1
    e=$(grep -m1 -E "^(PIN_ERRO|WAN_ERRO|LAT_ERRO)" $d/resultado.txt 2>/dev/null) && { log "ABORTA $modo: $e"; exit 1; }
    log "fim $modo ($(grep -c '^modo=' $d/resultado.txt 2>/dev/null) runs; $(grep '^modo=' $d/resultado.txt | grep -o 'workers_por_shard=[0-9]*\|d_tps=[0-9]*\|incompletas_d=[0-9]*\|sd_avg=[0-9.]*ms' | paste -sd' ' | sed 's/workers_por_shard=/| w=/g'))"
done <<'LISTA'
bft 40 50 65 80
cft 1500
LISTA
log "FIM_WAN_EXTRA"
