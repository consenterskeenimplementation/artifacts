# Artifacts: ordering-based atomic cross-channel transactions (Skeen consenter)

This repository contains everything needed to check and reproduce the evaluation of the paper *An Ordering-Based BFT
Protocol for Atomic Cross-Chain Transactions*:

- the load client and the correctness verifiers;
- the scripts of every benchmark battery reported in the paper;
- the raw results of those batteries, and the scripts that turn them into the paper's figures;
- the cross-platform prototype (a Hyperledger Fabric network and a CometBFT blockchain in the same atomic transaction).

The protocol itself, a consenter named `skeen` for Hyperledger Fabric, is in the companion repository
**[consenterskeenimplementation/fabric](https://github.com/consenterskeenimplementation/fabric)**, branch `skeen`. It is a fork of Hyperledger Fabric
with a single commit on top of upstream (`46810ca0f`, right after the v3.1.4 release), so `git diff 46810ca0f skeen`
shows exactly what was added. See `orderer/consensus/skeen/README.md` there.

The comments inside the scripts are in Portuguese. This README and the Fabric fork's README describe everything a
reviewer needs.

## Layout

| Path | Content |
|---|---|
| `preparar.sh` | Builds everything (orderer and tools from the fork, client, CometBFT side) and generates the orderer crypto material |
| `testar_alg3_local.sh` | `go vet` + `go test -race` of the consenter in the fork |
| `reproduzir_figuras.sh` | Regenerates Figs. 3–6 of the paper from the raw data in `resultados/` |
| `bancada.sh` | Assembles the flat working directory used by the benchmark batteries |
| `cliente_e2e/` | Load client (Fabric `Broadcast`/`Deliver`; closed loop with N workers, optional rate cap, one client per site in the WAN runs) |
| `configtx.yaml`, `orderer.yaml`, `crypto-config*.yaml` | Channel profiles (Skeen-pure, Skeen-CFT, Skeen-BFT, native Raft and SmartBFT), orderer configuration, `cryptogen` input |
| `resultados/sh/` | Benchmark batteries (`rodar_*.sh` orchestrators, `teste*.sh` per-configuration runners) and the local correctness test |
| `resultados/scripts/` | Latency aggregation (`txtiming_agrega_d.py`), outlier rule, battery validation, network emulation (`latsetter.py`), figure scripts |
| `resultados/2026-*/brutos/` | Raw results of the batteries used in the paper (see the table under "Reproducing the figures") |
| `resultados/wan/lat_ec2.csv` | Inter-region one-way latencies used for the WAN emulation |
| `cometbft/` | Skeen as an ABCI++ application of CometBFT v0.38.26 and the Fabric × CometBFT prototype |

## Requirements

- Linux, Go 1.26.1 (required by the `go.mod` files), Python 3.12 with `pip install -r requirements.txt`.
- For the benchmark batteries: `tc`/netem and root (emulated LAN/WAN latency on the loopback), `taskset`, and a machine
  comparable to the one used in the paper (ARM64, 160 cores, 2 NUMA nodes). The correctness tests and the figure
  reproduction run on any Linux machine.

## 1. Build

```
git clone -b skeen https://github.com/consenterskeenimplementation/fabric.git
git clone https://github.com/consenterskeenimplementation/artifacts.git
cd artifacts
bash preparar.sh          # FABRIC_DIR=../fabric by default
```

`preparar.sh` puts `orderer_alg3_local`, `configtxgen`, `osnadmin`, `cryptogen` and `cliente_e2e_bin` in `bin/`, the
CometBFT binaries in `cometbft/bin/`, and copies the orderer to `../fabric/build/bin/` under the names used by the
battery scripts (`orderer_alg3i`, `orderer_alg3k`: names of the builds used in the paper runs).

## 2. Correctness tests

Unit and regression tests of the consenter (Algorithms 1 and 3, certificates, view change, input validation):

```
bash testar_alg3_local.sh
(cd cometbft/skeenabci && go test -race ./...)
```

Local test with real processes: 4 channels, cross-channel transactions to 3 destinations, every replica records its
delivery order, and `resultados/scripts/verifica_ordem_cross.py` checks the properties of atomic multicast:
integrity, agreement, acyclic global order, completeness, and real-time order of local transactions (C10).

```
MODE=cft  DEST=3 NSH=4 bash resultados/sh/teste_alg3_local_dest.sh 10 0.5 20   # Skeen-CFT (etcd/raft)
MODE=bft  DEST=3 NSH=4 bash resultados/sh/teste_alg3_local_dest.sh 10 0.5 20   # Skeen-BFT (SmartBFT)
MODE=puro DEST=3 NSH=4 bash resultados/sh/teste_alg3_local_dest.sh 10 0.5 20   # Skeen-pure (no consensus)
KILL=1 MODE=cft DEST=3 NSH=4 bash resultados/sh/teste_alg3_local_dest.sh 10 0.5 20   # coordinator crash mid-run
```

Expected output: `VEREDITO: OK` and `hash de ordem ... ok` for every channel (the same order hash at all replicas).

The real-time check compares wall-clock timestamps of different processes. On virtual machines whose clock jumps (WSL,
for instance), it can report a false violation. The verifier detects the usual case (`AVISO relógio`) and widens the
margin; the order hashes are not affected by the clock.

## 3. Reproducing the figures

```
bash reproduzir_figuras.sh            # writes figuras_<date>/ ; LABELS=1 draws the panel labels inside the panels
```

The panels come out exactly as used in the paper. The paper adds the panel labels ("1 channel", "5%", "WAN
configuration", "Skeen-BFT") with TikZ.

| Figure | Raw data | Script |
|---|---|---|
| 3 (no cross-channel, 1/4/6 channels) | `resultados/2026-10-10_run_semcross_1_4_6shards/brutos/` (+ BFT series below) | `grafico_151_figuras_lan.py` |
| 4, 5 (4 and 6 channels, 5/10/20% cross-channel, 2 and 3 destinations) | `resultados/2026-10-08_run_cross_4e6shards/brutos/` | `grafico_151_figuras_lan.py` |
| Skeen-BFT and SmartBFT series of Figs. 3–5 (5 cores per replica) | `resultados/2026-10-13_run_bft5nucleos/brutos/` | `grafico_151_figuras_lan.py` |
| 6 (WAN: map, latency × throughput, CDF per region pair) | `resultados/2026-11-05_run_wan/brutos/` | `grafico_152_figuras_wan.py`, `grafico_wan_unido.py`, `grafico_cdf_wan.py` |

Each `seq*_<ddmm_HHMM>.txt` file is one periodic pull of the battery's result file from the server. The pulls are
cumulative, and the figure scripts read all of them in chronological order, so a re-executed run replaces the earlier
one. Each line holds one run: mode, number of channels, % cross-channel, clients per channel, run number, committed
throughput (`d_tps`), incomplete transactions and latency statistics. Latency is measured from the client's send to
the delivery at the last destination: the D mark, taken between lines 24 and 25 of Algorithm 1.

The methodology common to all batteries:

- 60-s runs with 10 s of warm-up, 3 runs per load level;
- the (level, run) pairs are executed in a random order with a fixed seed;
- the median of the 3 runs is plotted;
- outlier runs are re-executed (`fora_da_curva.py`);
- replicas and clients are pinned to dedicated cores (`pin_cpu.py` checks the pinning in every run);
- 2 ms emulated LAN; no disk writes during the measurement, applied equally to Skeen and to the native services.

## 4. Re-running the batteries

```
bash bancada.sh ../bench            # ../bench must be next to ../fabric
cd ../bench
sudo -E nohup bash rodar_semcross.sh > /dev/shm/rodar_semcross.log 2>&1 &
```

Each orchestrator writes its progress to `/dev/shm/seq_<name>.txt` (it ends with a `FIM_...` line) and the results to
`/dev/shm/<battery>/resultado.txt`. That file has the same format as the raw data in `resultados/`.

| Orchestrator | Battery (paper) |
|---|---|
| `rodar_semcross.sh` | No cross-channel transactions, 1/4/6 channels, all protocols (Fig. 3) |
| `rodar_cross46.sh`, `rodar_cross46_ext.sh` | 4 and 6 channels, 2/3 destinations, 5/10/20% cross-channel, and the extension to higher loads (Figs. 4–5) |
| `rodar_bft5.sh` | Skeen-BFT and native SmartBFT with 5 cores per replica (BFT series of Figs. 3–5) |
| `rodar_wan.sh`, `rodar_wan_extra.sh`, `rodar_wan_refaz.sh` | WAN, 4 channels (São Paulo, N. Virginia, Frankfurt, Tokyo), 10% cross-channel (Fig. 6) |
| `rodar_wan_cftbt.sh` | WAN, Skeen-CFT with a 50 ms batch timeout (the Skeen-CFT curve of Fig. 6) |
| `rodar_wan_cdf_igual.sh`, `rodar_wan_pares.sh` | WAN latency distribution per pair of regions (CDF of Fig. 6) |

## 5. Cross-platform prototype: Fabric × CometBFT

`cometbft/skeenabci/` implements the same Algorithm 3 as an ABCI++ application of CometBFT. A global transaction is sent
to a Fabric channel and to a CometBFT blockchain with the same TxID, and the coordinators of the two platforms exchange
certificates in the same format:

- msgpack encoding, SHA-256 digest;
- the Fabric orderers sign with ECDSA and their MSP identities, verified by CometBFT against pinned x509 certificates;
- the CometBFT validators sign with Ed25519, verified by Fabric through `SKEEN_FOREIGN`.

```
DRENO=30 W=4 CROSS=0.3 DUR=20 bash cometbft/rodar_hetero_local.sh /tmp/het bin/orderer_alg3_local bin/cliente_e2e_bin
SO_CMT=0.5 ... (same)        # half of the global transactions sent only to CometBFT: tests the START relay to Fabric
bash cometbft/varredura_hetero.sh /tmp/sweep    # controlled-load sweep (cometbft/resultados/varredura_hetero_20261118.txt)
bash cometbft/rodar_local.sh                      # CometBFT-only: several CometBFT blockchains
```

`cometbft/verifica_hetero.py` checks agreement and integrity on each platform, completeness of the global transactions,
and that the common global transactions are delivered in the same relative order on Fabric and on CometBFT.

Simplifications of the prototype:

- the trust roots (the orderers' certificates and the validators' keys) and the set of groups are configured
  statically on both sides;
- the CometBFT coordinator is fixed (replica 0), and only it re-broadcasts DELIVER_LOCAL;
- CometBFT does not verify the client's signature on the Fabric envelope it relays (Fabric does);
- Fabric keeps the certificates of delivered transactions for 1 minute, which bounds recovery after a long outage.

## Notes on the binaries used in the paper

The paper's runs used earlier builds of the same consenter (`orderer_alg3i` for the LAN batteries, `orderer_alg3k` for
the WAN batteries). The code published here adds, on top of them:

- `SKEEN_FOREIGN`/`SKEEN_ALG3_GROUPS` for the cross-platform prototype; they are unset in the batteries;
- an unambiguous cache key for verified certificates;
- rejection of `|`, `@` and whitespace in cross-channel TxIDs;
- certificates sent only to destinations of the transaction in NEED replies.

None of these changes the behavior of the benchmark workloads.

## License

Apache License 2.0 (see `LICENSE`).
