#!/bin/bash
# Skeen-CFT na WAN, 10% cross, com BatchTimeout menor (17/11/2026: com 200 ms o ponto de 25 clientes/canal
# ainda tem latência maior que o de 100, porque com ~1250 tx/s por canal o lote de 500 nunca enche e cada global espera o
# tempo limite em 2 consensos). BT=50ms por padrão; teste110w.sh com BATCH_TIMEOUT (md5 27369e1d…, já no servidor).
# Mesma configuração da rodar_wan.sh (orderer_alg3k, cliente_e2e_wan_bin, 3 runs de 60 s, MAX_REFAZ=2), sufixo SUF (_wc).
# Log: /dev/shm/seq_wan_cftbt.txt e resultados_d/seq_wan_cftbt.txt; marcador FIM_WAN_CFTBT.
cd "$(dirname "$0")"
BT=${BT:-50ms}; SUF=${SUF:-_wc}
P=/dev/shm/seq_wan_cftbt.txt; PD=resultados_d/seq_wan_cftbt.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
pgrep -f '[b]uild/bin/orderer' > /dev/null && { log "ABORTA orderer rodando"; exit 1; }
md5sum teste110w.sh | grep -q 27369e1d02143417a2d6dd8d0b03a7c9 || { log "ABORTA teste110w.sh com md5 inesperado"; exit 1; }
d=/dev/shm/g110w_wan_c01_d2_cft4$SUF; rm -rf $d
log "inicio cft wan cross=0.1 BatchTimeout=$BT niveis={25 100 300 600 1000 1500}"
BATCH_TIMEOUT=$BT COND=wan CROSS=0.1 DEST=2 SUFIXO=$SUF NRUNS=3 DURATION=60 MAX_REFAZ=2 WORKERS_LIST="25 100 300 600 1000 1500" bash teste110w.sh cft 4 > /dev/null 2>&1
grep -q "BatchTimeout: $BT" $d/cfg/configtx.yaml 2>/dev/null && log "conferido: configtx com BatchTimeout $BT" || log "ALERTA configtx sem BatchTimeout $BT"
log "fim cft ($(grep -c '^modo=' $d/resultado.txt 2>/dev/null) runs)"
log "FIM_WAN_CFTBT"
