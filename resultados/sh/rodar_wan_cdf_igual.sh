#!/bin/bash
# CDF das tx globais do Skeen-BFT na WAN com a MESMA CARGA em todas as configurações (13/11/2026). Na bateria de 12/11
# (rodar_wan_cdf.sh) as curvas dos pares cruzavam porque cada par teve uma carga diferente: a calibração bateu no teto
# (fator 3,5 = 350 workers nos clientes do par, 100 nos outros), a fatia global ficou entre 5,9% e 6,7% em vez de 10%, a
# vazão dos canais do par variou de ~1600 a ~3000 tx/s, e runs travados deixaram 1 a 3 runs por par.
# Aqui: 4 canais (um por região), 100 clientes por canal (como nas baterias de LAN e de WAN), cada cliente com teto de
# RATE tx/s (cliente -rate), de modo que os 4 canais recebem a mesma vazão em todas as configurações:
#   ig<a><b>: tx globais só entre canal<a> e canal<b> (PAR; os 2 clientes do par com 2×10% de globais, os outros sem
#             globais: no total, 10% de globais), 2 destinos;
#   igtodas:  10% de tx globais em todos os clientes, cada uma para os 4 canais (DEST=4) = "todas as regiões";
#   igsort:   10% de tx globais em todos os clientes, cada uma para 2 canais com o par sorteado entre todas as regiões
#             (13/11/2026, a curva "All regions" pedida: fica entre os pares). SO=<sufixo> roda só essa configuração.
# 3 runs de 60 s por configuração; um run com fatia global < 70% da do melhor run (travado) ganha um run extra (até 2).
# Amostras em $AM; no fim, cdf_quantis.py (todos os runs e sem os travados). Log: /dev/shm/seq_wan_cdf_igual.txt e
# resultados_d/seq_wan_cdf_igual.txt (marcador final FIM_WAN_CDF_IGUAL<SO>). Quantis também por par dentro de cada
# configuração (--por-par). Uso: [RATE=1000] [SO=_igsort] nohup bash rodar_wan_cdf_igual.sh &
cd "$(dirname "$0")"
P=/dev/shm/seq_wan_cdf_igual.txt; PD=resultados_d/seq_wan_cdf_igual.txt; mkdir -p resultados_d
log() { echo "$1 $(date '+%d/%m %T')" | tee -a $P >> $PD; }
B=../fabric/build/bin
for f in $B/orderer_alg3k ./cliente_e2e_wan3_bin; do [ -x $f ] || { log "ERRO falta $f"; exit 1; }; done
RATE=${RATE:-1000}; W=100
AM=$PWD/resultados_d/cdf_igual_$(date +%d%m_%H%M); mkdir -p $AM
log "WAN_CDF_IGUAL_INICIO${SO:+ so=$SO} alg3k md5=$(md5sum $B/orderer_alg3k | cut -c1-12) cliente_wan3 md5=$(md5sum cliente_e2e_wan3_bin | cut -c1-12) teste110w=$(md5sum teste110w.sh | cut -c1-12) rate=$RATE clientes/canal=$W amostras=$AM"
fatia() {  # fatia global (x_n / d_n, em %) de cada run registrado em $1
    grep '^modo=' $1 | awk '{x=$0; sub(/.*x_n=/,"",x); sub(/ .*/,"",x); d=$0; sub(/.*d_n=/,"",d); sub(/ .*/,"",d); printf "%.1f ", (d > 0) ? 100*x/d : 0}'
}
roda() {  # $1 sufixo, $2 PAR ("" = nenhum), $3 DEST, $4 runs; acumula os resultados em $d.todos
    local d=/dev/shm/g110w_wan_c01_d$3_bft4$1
    rm -rf $d
    TXTIMING_AMOSTRAS_DIR=$AM CLI_BIN=./cliente_e2e_wan3_bin RATE=$RATE PAR=$2 PAR_FATOR=1 COND=wan CROSS=0.1 DEST=$3 \
        SUFIXO=$1 NRUNS=$4 DURATION=60 MAX_REFAZ=0 WORKERS_LIST=$W bash teste110w.sh bft 4 > /dev/null 2>&1
    local e; e=$(grep -m1 -E "^(PIN_ERRO|WAN_ERRO|LAT_ERRO)" $d/resultado.txt 2>/dev/null) && { log "ABORTA $1: $e"; exit 1; }
    grep -q '^modo=' $d/resultado.txt 2>/dev/null || { log "ABORTA $1: nenhum run registrado"; exit 1; }
    grep '^modo=' $d/resultado.txt >> $d.todos
}
while read -r suf par dest; do
    [ -z "$suf" ] && continue
    [ -n "${SO:-}" ] && [ "$suf" != "$SO" ] && continue
    [ "$par" = - ] && par=""
    d=/dev/shm/g110w_wan_c01_d${dest}_bft4$suf; rm -f $d.todos
    log "inicio $suf par=${par:-todas} dest=$dest rate=$RATE"
    roda $suf "$par" $dest 3
    for extra in 1 2; do  # run travado (fatia global < 70% do melhor run): mais um run
        f=$(fatia $d.todos); bons=$(echo $f | awk '{m=0; for(i=1;i<=NF;i++) if($i>m) m=$i; b=0; for(i=1;i<=NF;i++) if($i>=0.7*m) b++; print b}')
        [ "$bons" -ge 3 ] && break
        log "travado $suf (fatias: $f): run extra $extra"
        roda $suf "$par" $dest 1
    done
    log "fim $suf (fatia global por run: $(fatia $d.todos); tps_por_local: $(grep -o 'tps_por_local=[0-9/]*' $d.todos | cut -d= -f2 | tr '\n' ' '); x_sd_avg: $(grep -o 'x_sd_avg=[0-9.]*' $d.todos | cut -d= -f2 | tr '\n' ' '))"
    rm -f $d/cli*.err
done <<'EOF'
_igtodas - 4
_ig12 canal1-canal2 2
_ig13 canal1-canal3 2
_ig14 canal1-canal4 2
_ig23 canal2-canal3 2
_ig24 canal2-canal4 2
_ig34 canal3-canal4 2
_igsort - 2
EOF
python3 cdf_quantis.py $AM --por-par | grep 'tipo=globais' | tee -a $P >> $PD
python3 cdf_quantis.py $AM --descarta-travados --por-par | grep -E 'CDF_DESCARTE|tipo=globais' | sed 's/^CDF tag=/CDF_SEM tag=/' | tee -a $P >> $PD
log "FIM_WAN_CDF_IGUAL${SO:-}"
