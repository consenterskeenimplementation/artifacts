#!/bin/bash
# Skeen entre 2 blockchains CometBFT, local (16/11/2026). Gera as blockchains A (127.0.1.1–4) e B (127.0.2.1–4), cada uma
# com 4 validadores (f=1) e chain_id próprio; escreve a configuração do Skeen de cada réplica (endereços e chaves
# públicas ed25519 dos validadores das duas blockchains; coordenador = réplica 0); sobe os 8 nós (skeen-node), roda o
# cliente (locais alternando A/B, globais para A e B) e o verificador (acordo, integridade, completude, ordem).
# Sintonia (16/11/2026): com os padrões do CometBFT (flush_throttle_timeout e peer_gossip_sleep_duration de 100 ms) cada
# bloco levava ~510 ms; com 10 ms nos dois e timeout_commit de 100 ms, ~160 ms. Valores declarados no texto.
# CHAINS=2 (A, B) ou 4 (A–D, IPs 127.0.<k>.1–4); DEST = destinos por global (casa + sorteadas; padrão: todas).
# PARCIAL=p manda uma fração p das globais só ao 1º destino (os outros recebem o START pelo relay).
# SKEEN_DROP=p descarta de propósito uma fração p das mensagens entre réplicas (teste da recuperação por NEED).
# Uso: [CHAINS=2] [DEST=] [N=2000] [CROSS=0.1] [RATE=200] [COMMIT=100ms] [FLUSH=10ms] [GOSSIP=10ms] bash cometbft/rodar_local.sh <dir>
set -u
cd "$(dirname "$0")"
W=${1:?dir de trabalho}; N=${N:-2000}; CROSS=${CROSS:-0.1}; RATE=${RATE:-200}; COMMIT=${COMMIT:-100ms}; FLUSH=${FLUSH:-10ms}; GOSSIP=${GOSSIP:-10ms}
CHAINS=${CHAINS:-2}; DEST=${DEST:-0}; NOMES=$(echo A B C D | cut -d' ' -f1-$CHAINS)
rm -rf "$W"; mkdir -p "$W/out"
k=0; for g in $NOMES; do
    k=$((k+1)); base=$k
    ./bin/cometbft testnet --v 4 --o "$W/$g" --starting-ip-address "127.0.$base.1" > /dev/null 2>&1 || { echo "ERRO testnet $g"; exit 1; }
    for n in 0 1 2 3; do
        h="$W/$g/node$n"; ip="127.0.$base.$((n+1))"
        sed -i "s/\"chain_id\": \"[^\"]*\"/\"chain_id\": \"skeen-$g\"/" "$h/config/genesis.json"
        sed -i "s#^laddr = \"tcp://0.0.0.0:26656\"#laddr = \"tcp://$ip:26656\"#; s#^laddr = \"tcp://127.0.0.1:26657\"#laddr = \"tcp://$ip:26657\"#; s#^pprof_laddr = .*#pprof_laddr = \"\"#; s#^addr_book_strict = true#addr_book_strict = false#; s#^allow_duplicate_ip = false#allow_duplicate_ip = true#; s#^timeout_commit = .*#timeout_commit = \"$COMMIT\"#; s#^log_level = .*#log_level = \"error\"#; s#^flush_throttle_timeout = .*#flush_throttle_timeout = \"$FLUSH\"#; s#^peer_gossip_sleep_duration = .*#peer_gossip_sleep_duration = \"$GOSSIP\"#" "$h/config/config.toml"
    done
done
python3 - "$W" $NOMES <<'EOF'
import json, sys
W = sys.argv[1]
NOMES = sys.argv[2:]
chains = {}
for base, g in enumerate(NOMES, 1):
    reps = []
    for n in range(4):
        k = json.load(open(f"{W}/{g}/node{n}/config/priv_validator_key.json"))
        reps.append({"addr": f"127.0.{base}.{n+1}:27000", "pubkey": k["pub_key"]["value"]})
    chains[g] = {"replicas": reps, "rpc": f"http://127.0.{base}.1:26657"}
for base, g in enumerate(NOMES, 1):
    for n in range(4):
        json.dump({"chain": g, "listen": f"127.0.{base}.{n+1}:27000", "f": 1, "chains": chains},
                  open(f"{W}/{g}/node{n}/skeen.json", "w"), indent=1)
EOF
PIDS=""
for g in $NOMES; do for n in 0 1 2 3; do
    ./bin/skeen-node --home "$W/$g/node$n" --skeen-config "$W/$g/node$n/skeen.json" --out-dir "$W/out" > "$W/$g/node$n.log" 2>&1 &
    PIDS="$PIDS $!"
done; done
trap 'kill $PIDS 2>/dev/null' EXIT
sleep 6
echo "cliente: N=$N cross=$CROSS rate=$RATE tx/s commit=$COMMIT flush=$FLUSH gossip=$GOSSIP"
RPCS=$(k=0; for g in $NOMES; do k=$((k+1)); printf "%s=http://127.0.%d.2:26657," $g $k; done); RPCS=${RPCS%,}
./bin/skeen-cliente -rpc "$RPCS" -n "$N" -cross "$CROSS" -rate "$RATE" -dest "$DEST" -parcial "${PARCIAL:-0}" -out "$W/out/envios.txt"
sleep ${DRENO:-8}   # drenagem (DRENO: segundos; maior com SKEEN_DROP)
kill $PIDS 2>/dev/null; wait 2>/dev/null; trap - EXIT
python3 verifica.py "$W/out" "$W/out/envios.txt"
