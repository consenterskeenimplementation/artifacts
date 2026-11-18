#!/bin/bash
# Reexecuta os pontos que "voltam" no gráfico wan_lat_vazao_20261112 (12/11/2026): em cada uma
# das 12 curvas da bateria rodar_wan.sh, os níveis de clientes/canal acima do de maior vazão (onde a curva volta para a
# esquerda), 3 runs cada, com o mesmo binário, cliente e script. Os runs novos entram na mesma pasta da curva
# (/dev/shm/g110w_<cond>_c<cross>_d2_<modo>4_w, marcados REFAZ_FINAL e refeita=3) e substituem os antigos nos gráficos.
# Níveis, a partir das medianas da puxada seqwan_1211_0815.txt (nível de maior vazão entre parênteses):
#   10%: puro controle (1000) 2000 4000 | puro wan (2000) 4000 | cft controle (1000) 2000 4000 | cft wan (1000) 2000 4000
#        bft controle (300) 600 1000 2000 | bft wan (300) 600 1000 2000
#   20%: puro controle (1000) 2000 4000 | puro wan (2000) 4000 | cft controle (1000) 2000 4000 | cft wan (600) 1000 2000 4000
#        bft controle (100) 300 600 1000 2000 | bft wan (300) 600 1000 2000
# Log: /dev/shm/seq_wan_refaz.txt e resultados_d/seq_wan_refaz.txt. Uso: nohup bash rodar_wan_refaz.sh &
cd "$(dirname "$0")"
P=/dev/shm/seq_wan_refaz.txt; PD=resultados_d/seq_wan_refaz.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
B=../fabric/build/bin
for f in $B/orderer_alg3k ./cliente_e2e_wan_bin; do [ -x $f ] || { log "ERRO falta $f"; exit 1; }; done
log "WAN_REFAZ_INICIO alg3k md5=$(md5sum $B/orderer_alg3k | cut -c1-12) cliente_wan md5=$(md5sum cliente_e2e_wan_bin | cut -c1-12) teste110w=$(md5sum teste110w.sh | cut -c1-12)"
while read -r cross modo cond niveis; do
    [ -z "$cross" ] && continue
    d=/dev/shm/g110w_${cond}_c$(echo $cross | tr -d .)_d2_${modo}4_w
    [ -f $d/resultado.txt ] || { log "ABORTA falta $d (servidor reiniciou?)"; exit 1; }
    pares=$(for w in $niveis; do echo "$w:1 $w:2 $w:3"; done | tr '\n' ' ')
    n0=$(grep -cE "^(PIN_ERRO|WAN_ERRO|LAT_ERRO)" $d/resultado.txt)
    log "inicio $modo cross=$cross cond=$cond niveis={$niveis}"
    PARES="$pares" RODADA0=3 MAX_REFAZ=0 COND=$cond CROSS=$cross DEST=2 SUFIXO=_w DURATION=60 bash teste110w.sh $modo 4 > /dev/null 2>&1
    [ "$(grep -cE "^(PIN_ERRO|WAN_ERRO|LAT_ERRO)" $d/resultado.txt)" -gt "$n0" ] && {
        log "ABORTA $modo cross=$cross cond=$cond: $(grep -E "^(PIN_ERRO|WAN_ERRO|LAT_ERRO)" $d/resultado.txt | tail -1)"; exit 1; }
    log "fim $modo cross=$cross cond=$cond ($(grep -c 'refeita=3' $d/resultado.txt) runs refeitos)"
done <<'EOF'
0.1 puro controle 2000 4000
0.1 puro wan 4000
0.1 cft controle 2000 4000
0.1 cft wan 2000 4000
0.1 bft controle 600 1000 2000
0.1 bft wan 600 1000 2000
0.2 puro controle 2000 4000
0.2 puro wan 4000
0.2 cft controle 2000 4000
0.2 cft wan 1000 2000 4000
0.2 bft controle 300 600 1000 2000
0.2 bft wan 600 1000 2000
EOF
log "FIM_WAN_REFAZ"
