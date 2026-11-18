#!/usr/bin/env python3
"""Confere o pinning de núcleos e mede o uso de CPU dos processos de uma bateria (08/10/2026).

  pin_cpu.py confere <arquivo>   arquivo com linhas "nome pid núcleos" (ex.: "s1r1 12345 0-5", "cliente 999 104-159").
                                 Para cada processo confere, em TODAS as threads (/proc/<pid>/task/*/status), que a
                                 afinidade (Cpus_allowed_list) é exatamente a planejada, e que os conjuntos planejados
                                 não se sobrepõem. Também lista processos de fora com CPU alta nos núcleos usados.
                                 Imprime "pin=ok ..." ou "pin=ERRO <motivo>" (código de saída 1).
  pin_cpu.py ticks <pid>...      imprime utime+stime (em ticks) de cada pid, na mesma ordem (0 se o processo sumiu).
  pin_cpu.py uso <dt> <a> <b> <n_cli> <n_rep> [k]
                                 a, b = saídas de "ticks" no início e no fim da janela (o 1º pid é o cliente); imprime
                                 núcleos usados pelo cliente, máximo e média por réplica, e o cliente saturado (> 85%).
"""
import glob
import os
import sys

HZ = os.sysconf("SC_CLK_TCK")


def expande(lista):
    s = set()
    for parte in lista.strip().split(","):
        if not parte:
            continue
        if "-" in parte:
            a, b = parte.split("-")
            s.update(range(int(a), int(b) + 1))
        else:
            s.add(int(parte))
    return s


def afinidade(path):
    for linha in open(path):
        if linha.startswith("Cpus_allowed_list:"):
            return expande(linha.split(":", 1)[1])
    return None


def ticks(pid):
    try:
        campos = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        return int(campos[11]) + int(campos[12])  # utime, stime (campos 14 e 15 do stat)
    except (OSError, IndexError, ValueError):
        return 0


def confere(arq):
    plano, erros, nthreads = [], [], 0
    for linha in open(arq):
        p = linha.split()
        if len(p) == 3:
            plano.append((p[0], int(p[1]), expande(p[2])))
    usados = {}
    for nome, pid, cores in plano:
        for c in cores:
            if c in usados:
                erros.append(f"núcleo {c} planejado para {usados[c]} e {nome}")
            usados[c] = nome
        tasks = glob.glob(f"/proc/{pid}/task/*/status")
        if not tasks:
            erros.append(f"{nome} (pid {pid}) não existe")
            continue
        for t in tasks:
            a = afinidade(t)
            nthreads += 1
            if a is not None and a != cores:
                erros.append(f"{nome} thread {t.split('/')[4]} com afinidade {sorted(a)[:3]}… ≠ planejada")
                break
    # processos de fora (outros usuários ou sobras) consumindo CPU agora: amostra rápida de 1 s
    nossos = {pid for _, pid, _ in plano}
    antes = {}
    for d in glob.glob("/proc/[0-9]*"):
        pid = int(d.rsplit("/", 1)[1])
        if pid not in nossos:
            antes[pid] = ticks(pid)
    import time
    time.sleep(1)
    outros = []
    for pid, t0 in antes.items():
        dt = (ticks(pid) - t0) / HZ
        if dt > 0.3:
            try:
                comm = open(f"/proc/{pid}/comm").read().strip()
            except OSError:
                comm = "?"
            outros.append(f"{comm}:{dt:.1f}")
    resumo = f"{len(plano)} processos, {nthreads} threads, {len(usados)} núcleos, outros={','.join(outros) or 'nenhum'}"
    if erros:
        print("pin=ERRO " + "; ".join(erros[:5]) + " | " + resumo)
        sys.exit(1)
    print("pin=ok " + resumo.replace(" ", "_"))


def uso(dt, a, b, n_cli, n_rep, k="1"):
    """k = nº de processos-cliente (os k primeiros pids), cada um com n_cli núcleos; saturado = algum acima de 85%."""
    a, b, dt, k = [int(x) for x in a.split()], [int(x) for x in b.split()], float(dt), int(k)
    cores = [(y - x) / HZ / dt for x, y in zip(a, b)]
    clis, reps = cores[:k], cores[k:]
    sat = "sim" if max(clis) > 0.85 * int(n_cli) else "não"
    print(f"cpu_cliente={sum(clis):.1f}/{k * int(n_cli)} cliente_saturado={sat} cpu_rep_max={max(reps):.1f}/{n_rep} "
          f"cpu_rep_media={sum(reps) / len(reps):.1f}/{n_rep}")


if __name__ == "__main__":
    if sys.argv[1] == "confere":
        confere(sys.argv[2])
    elif sys.argv[1] == "ticks":
        print(" ".join(str(ticks(p)) for p in sys.argv[2:]))
    elif sys.argv[1] == "uso":
        uso(*sys.argv[2:8])
