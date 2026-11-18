#!/usr/bin/env python3
# RTT médio entre dois locais do teste de WAN, em ms, sem ping (o contêiner do servidor não tem): tempo de um connect TCP
# a uma porta fechada, com a origem no IP do local. O SYN (origem -> destino) e o RST (destino -> origem) passam pelas
# regras do netem de cada sentido, como o ICMP. Qualquer outra resposta (timeout, IP inexistente) é erro: sai com 1.
# Uso: wan_rtt.py <ip origem> <ip destino> [n]
import socket
import sys
import time

PORTA_FECHADA = 9

src, dst = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 3
total = 0.0
for _ in range(n):
    s = socket.socket()
    s.settimeout(5)
    s.bind((src, 0))
    t0 = time.perf_counter()
    try:
        s.connect((dst, PORTA_FECHADA))
        sys.exit(f"porta {PORTA_FECHADA} aberta em {dst}")
    except ConnectionRefusedError:
        total += time.perf_counter() - t0
    finally:
        s.close()
print(f"{1000 * total / n:.3f}")
