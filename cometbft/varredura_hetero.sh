#!/bin/bash
# Varredura em carga controlada do Skeen Fabric × CometBFT (17/11/2026; passo 2 do PLANO_HETERO): cada nível de -rate
# (tx/s oferecidas pelo cliente, laço aberto com teto) roda RUNS vezes, pares (nível, run) em ordem aleatória com semente
# fixa; cada execução guarda o resumo do verificador e a latência no arquivo de sequência.
# Uso (da raiz): [NIVEIS="100 200 400 800 1200"] [RUNS=3] [W=64] [CROSS=0.3] [DUR=30] bash cometbft/varredura_hetero.sh <dir>
set -u
D=${1:?dir}; NIVEIS=${NIVEIS:-"100 200 400 800 1200"}; RUNS=${RUNS:-3}
B=$PWD/bin; mkdir -p "$D"; SEQ="$D/seq.txt"
PARES=$(for n in $NIVEIS; do for r in $(seq 1 "$RUNS"); do echo "$n $r"; done; done | python3 -c 'import sys,random; l=sys.stdin.read().split("\n"); l=[x for x in l if x]; random.Random(1711).shuffle(l); print("\n".join(l))')
echo "inicio $(date +%F_%T) niveis=[$NIVEIS] runs=$RUNS W=${W:-64} cross=${CROSS:-0.3} dur=${DUR:-30}" >> "$SEQ"
while read -r n r; do
    T="$D/r${n}_$r"
    RATE=$n W=${W:-64} CROSS=${CROSS:-0.3} DUR=${DUR:-30} DRENO=${DRENO:-20} FABBIN=$B timeout 900 \
        bash cometbft/rodar_hetero_local.sh "$T" "$B/orderer_alg3_local" "$B/cliente_e2e_bin" > "$T.log" 2>&1
    echo "nivel=$n run=$r $(grep -E '^d_tps' "$T.log") $(grep -E 'TPS global' "$T.log" | tr -s ' ') $(grep VEREDITO "$T.log")" >> "$SEQ"
    rm -rf "$T/ledger" "$T/cmt"   # o que pesa; ficam rastros, marcas e sequências
done <<< "$PARES"
echo "FIM_VARREDURA $(date +%F_%T)" >> "$SEQ"
