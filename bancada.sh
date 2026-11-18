#!/bin/bash
# Assembles the flat working directory used by the benchmark scripts (resultados/sh/rodar_*.sh, teste*.sh). On the
# benchmark server all of them ran from one flat directory next to the Fabric fork:
#     <workspace>/fabric   the fork with the skeen consenter (orderer built into fabric/build/bin by preparar.sh)
#     <workspace>/bench    this directory: scripts, helper programs, channel configuration, crypto material, client
# Usage (from the repository root, after preparar.sh): bash bancada.sh <workspace>/bench
# then, on the server: cd <workspace>/bench && nohup bash rodar_semcross.sh > /dev/shm/rodar_semcross.log 2>&1 &
set -euo pipefail
cd "$(dirname "$0")"
D=${1:?target directory, e.g. ../bench (must be next to the fabric fork)}
mkdir -p "$D"
cp resultados/sh/*.sh resultados/sh/pin_cpu.py "$D/"
for f in latsetter fora_da_curva txtiming_agrega_d gera_configtx_wan wan_rtt wan_zonas wan_confere_conexoes cdf_quantis; do
    cp "resultados/scripts/$f.py" "$D/"
done
cp resultados/scripts/latsetter.py "$D/latsetter_wan.py"   # the WAN battery calls it by this name
cp resultados/wan/lat_ec2.csv configtx.yaml orderer.yaml crypto-config.yaml crypto-config-wan.yaml "$D/"
cp -r crypto-config crypto-config-wan "$D/"
cp bin/cliente_e2e_bin "$D/cliente_e2e_bin"
cp bin/cliente_e2e_bin "$D/cliente_e2e_wan_bin"            # same client; the WAN battery uses -home/-rate
[ -d "$D/../fabric/build/bin" ] || echo "WARNING: $D/../fabric/build/bin not found: put the bench next to the fork"
echo "bench ready in $D"
