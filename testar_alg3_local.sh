#!/bin/bash
# go vet + go test -race do consenter skeen na árvore do Fabric com o consenter (fork).
# Uso: [FABRIC_DIR=../fabric] bash testar_alg3_local.sh [regex de teste]   (padrão: todos)
set -e
cd "$(dirname "$0")"
cd "${FABRIC_DIR:-../fabric}"
go vet ./orderer/consensus/skeen/ ./orderer/common/txtiming/
go test -race -count=1 -run "${1:-.}" ./orderer/consensus/skeen/
