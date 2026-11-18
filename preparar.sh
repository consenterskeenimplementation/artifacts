#!/bin/bash
# Prepares everything needed by the tests and benchmarks of this repository.
#   1. builds, from the Fabric fork with the skeen consenter (FABRIC_DIR, default ../fabric), the orderer, configtxgen,
#      osnadmin and cryptogen into bin/;
#   2. builds the load client (cliente_e2e) into bin/cliente_e2e_bin;
#   3. builds the CometBFT side: cometbft/bin/skeen-node (Skeen as an ABCI++ application), cometbft/bin/skeen-cliente
#      and the cometbft CLI v0.38.26 (used only for "cometbft testnet");
#   4. generates the orderer crypto material (crypto-config/ and crypto-config-wan/) with cryptogen.
# Usage (from the repository root): [FABRIC_DIR=../fabric] bash preparar.sh
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)
F=$(cd "${FABRIC_DIR:-../fabric}" && pwd)
[ -d "$F/orderer/consensus/skeen" ] || { echo "FABRIC_DIR=$F is not the Fabric fork with the skeen consenter"; exit 1; }
mkdir -p bin cometbft/bin
echo "== Fabric fork: $F"
(cd "$F" && go build -o "$ROOT/bin/orderer_alg3_local" ./cmd/orderer \
         && go build -o "$ROOT/bin/configtxgen" ./cmd/configtxgen \
         && go build -o "$ROOT/bin/osnadmin" ./cmd/osnadmin \
         && go build -o "$ROOT/bin/cryptogen" ./cmd/cryptogen)
# the server scripts look for the orderer under FABRIC_DIR/build/bin with the names used in the paper runs
mkdir -p "$F/build/bin"
for n in orderer orderer_alg3i orderer_alg3k; do cp "$ROOT/bin/orderer_alg3_local" "$F/build/bin/$n"; done
cp bin/configtxgen bin/osnadmin bin/cryptogen "$F/build/bin/"
echo "== load client"
go build -o bin/cliente_e2e_bin ./cliente_e2e
echo "== CometBFT side"
(cd cometbft/skeenabci && go build -o ../bin/skeen-node . && go build -o ../bin/skeen-cliente ./cliente)
GOBIN="$ROOT/cometbft/bin" go install github.com/cometbft/cometbft/cmd/cometbft@v0.38.26
echo "== crypto material"
[ -d crypto-config ] || bin/cryptogen generate --config=crypto-config.yaml --output=crypto-config >/dev/null
[ -d crypto-config-wan ] || bin/cryptogen generate --config=crypto-config-wan.yaml --output=crypto-config-wan >/dev/null
ls bin cometbft/bin
echo "OK"
