#!/usr/bin/env python3

"""Simulate EC2-style inter-zone latencies on a Linux host using tc.

One-file Python script that emulates EC2-style inter-zone latencies on a
Linux host via tc (HTB root -> per-zone classes -> netem + u32 filters).
Run once per node with `set`; remove all rules with `unset`.
"""

import argparse
import csv
import fcntl
import os
import socket
import struct
import subprocess
import sys

RATE = "1gbit"
LOCAL_RATE = "10gbit"


def netem_args(lat: float, jitter_pct: float, limit: int | None) -> list[str]:
    """Argumentos do netem; jitter_pct=0 dá atraso fixo (sem reordenar pacotes) e limit=None mantém a fila padrão."""
    args = ["netem", "delay", f"{lat}ms"]
    delta = jitter_pct / 100 * lat
    if delta > 0:
        args += [f"{delta}ms", "distribution", "normal"]
    if limit is not None:
        args += ["limit", str(limit)]
    return args


def get_iface_ipv4(iface: str) -> str:
    """Return the IPv4 address bound to the given interface."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        addr = fcntl.ioctl(
            s.fileno(), 0x8915, struct.pack("256s", iface.encode()[:15])
        )[20:24]
    except OSError as e:
        raise ValueError(f"Could not find IP for interface {iface}: {e}") from e
    finally:
        s.close()
    return socket.inet_ntoa(addr)


def run_command(cmd_list: list[str], *, ignore_errors: bool = False) -> None:
    """Executes system commands. Script must be run as root."""
    try:
        subprocess.run(cmd_list, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            print(f"Error executing {' '.join(cmd_list)}: {e.stderr}")


def setup_root_qdisc(iface: str, rate: str) -> None:
    """Install the HTB root qdisc with the default class on the interface."""
    # Clear existing root (silently)
    run_command(["tc", "qdisc", "del", "dev", iface, "root"], ignore_errors=True)

    run_command(
        [
            "tc",
            "qdisc",
            "add",
            "dev",
            iface,
            "root",
            "handle",
            "1:",
            "htb",
            "default",
            "10",
            "r2q",
            "1000",
        ]
    )
    run_command(
        [
            "tc",
            "class",
            "add",
            "dev",
            iface,
            "parent",
            "1:",
            "classid",
            "1:1",
            "htb",
            "rate",
            rate,
        ]
    )
    run_command(
        [
            "tc",
            "class",
            "add",
            "dev",
            iface,
            "parent",
            "1:1",
            "classid",
            "1:10",
            "htb",
            "rate",
            rate,
        ]
    )
    run_command(
        [
            "tc",
            "qdisc",
            "add",
            "dev",
            iface,
            "parent",
            "1:10",
            "handle",
            "10:",
            "sfq",
            "perturb",
            "10",
        ]
    )


def set_local(
    destinations_file: str,
    protocol: str,
    dst_ip: str = "127.0.0.1",
    jitter_pct: float = 5.0,
    limit: int | None = None,
) -> None:
    """Install per-port latency rules on the loopback interface (traffic to dst_ip)."""
    with open(destinations_file, "r") as f:
        raw_rows: list[list[str]] = list(csv.reader(f))

    # Skip a header row if the first cell looks like "port".
    rows = raw_rows
    if rows and rows[0] and rows[0][0].strip().lower() == "port":
        rows = rows[1:]

    destinations: list[tuple[int, float]] = []
    seen_ports: set[int] = set()
    for row in rows:
        if not row:
            continue
        try:
            port = int(row[0])
            latency = float(row[1])
        except (ValueError, IndexError):
            print(f"# warning: invalid row {row}", file=sys.stderr)
            continue
        if port in seen_ports:
            print(
                f"# warning: duplicate port {port}; keeping the first",
                file=sys.stderr,
            )
            continue
        if latency < 0:
            print(
                f"# warning: negative latency {latency} for port {port}",
                file=sys.stderr,
            )
            continue
        if latency == 0:
            continue
        if not 1 <= port <= 65535:
            print(f"# warning: port {port} out of range", file=sys.stderr)
            continue
        seen_ports.add(port)
        destinations.append((port, latency))

    proto_num = 6 if protocol == "tcp" else 17

    setup_root_qdisc("lo", LOCAL_RATE)

    next_handle = 11
    for port, lat in destinations:
        delta = jitter_pct / 100 * lat

        run_command(
            [
                "tc",
                "class",
                "add",
                "dev",
                "lo",
                "parent",
                "1:1",
                "classid",
                f"1:{next_handle}",
                "htb",
                "rate",
                LOCAL_RATE,
            ]
        )
        run_command(
            [
                "tc",
                "qdisc",
                "add",
                "dev",
                "lo",
                "parent",
                f"1:{next_handle}",
                "handle",
                f"{next_handle}:",
                *netem_args(lat, jitter_pct, limit),
            ]
        )
        run_command(
            [
                "tc",
                "filter",
                "add",
                "dev",
                "lo",
                "protocol",
                "ip",
                "parent",
                "1:",
                "prio",
                "1",
                "u32",
                "match",
                "ip",
                "dst",
                f"{dst_ip}/32",
                "match",
                "ip",
                "protocol",
                str(proto_num),
                "0xff",
                "match",
                "ip",
                "dport",
                str(port),
                "0xffff",
                "flowid",
                f"1:{next_handle}",
            ]
        )

        print(f"# Setting latency to {lat}ms for port {port}")
        print(f"\t# Latency to {dst_ip}:{port} set to {lat} +/- {delta}")

        next_handle += 1

    print(f"# Applied {len(destinations)} latency rules on lo")


def read_matrix(lats_file: str) -> tuple[list[str], dict[tuple[str, str], float]]:
    """Zonas e latências (ms, um sentido) de uma matriz CSV com cabeçalho de zonas."""
    with open(lats_file, "r") as f:
        rows = [r for r in csv.reader(f) if r]
    zones = rows[0][1:]
    lat_map: dict[tuple[str, str], float] = {}
    for row in rows[1:]:
        for i, col in enumerate(zones, 1):
            try:
                lat_map[(row[0], col)] = float(row[i])
            except (ValueError, IndexError):
                print(f"# warning: invalid latency {row[0]} -> {col}", file=sys.stderr)
    return zones, lat_map


def set_lo_zones(
    ips_file: str,
    lats_file: str,
    lan_file: str | None,
    jitter_pct: float,
    limit: int | None,
    lan_jitter_pct: float,
) -> None:
    """Vários 'locais' num só host, cada um com um IP de loopback: entre IPs de zonas diferentes, atraso da matriz
    (filtro por origem E destino, prioridade 1); dentro do local, regras IP,porta,latência do lan_file (prioridade 2).
    Exige que quem envia entre locais saia pelo IP do próprio local (bind), senão a origem é 127.0.0.1."""
    with open(ips_file, "r") as f:
        ips = [(r["Zone"], r["IP"]) for r in csv.DictReader(f)]
    zones, lat_map = read_matrix(lats_file)
    setup_root_qdisc("lo", LOCAL_RATE)
    handle = 11
    n_wan = n_lan = 0
    for za, ipa in ips:
        for zb, ipb in ips:
            lat = lat_map.get((za, zb))
            if za == zb or not lat:
                continue
            run_command(["tc", "class", "add", "dev", "lo", "parent", "1:1", "classid", f"1:{handle}", "htb", "rate", RATE])
            run_command(["tc", "qdisc", "add", "dev", "lo", "parent", f"1:{handle}", "handle", f"{handle}:",
                         *netem_args(lat, jitter_pct, limit)])
            run_command(["tc", "filter", "add", "dev", "lo", "protocol", "ip", "parent", "1:", "prio", "1", "u32",
                         "match", "ip", "src", f"{ipa}/32", "match", "ip", "dst", f"{ipb}/32", "flowid", f"1:{handle}"])
            print(f"# {za} ({ipa}) -> {zb} ({ipb}): {lat} ms")
            handle += 1
            n_wan += 1
    if lan_file:
        with open(lan_file, "r") as f:
            for r in csv.DictReader(f):
                lat = float(r["Latency"])
                if lat <= 0:
                    continue
                run_command(["tc", "class", "add", "dev", "lo", "parent", "1:1", "classid", f"1:{handle}", "htb", "rate", LOCAL_RATE])
                run_command(["tc", "qdisc", "add", "dev", "lo", "parent", f"1:{handle}", "handle", f"{handle}:",
                             *netem_args(lat, lan_jitter_pct, None)])
                run_command(["tc", "filter", "add", "dev", "lo", "protocol", "ip", "parent", "1:", "prio", "2", "u32",
                             "match", "ip", "dst", f"{r['IP']}/32", "match", "ip", "protocol", "6", "0xff",
                             "match", "ip", "dport", r["Port"], "0xffff", "flowid", f"1:{handle}"])
                handle += 1
                n_lan += 1
    print(f"# Applied {n_wan} inter-zone and {n_lan} LAN rules on lo")


def main() -> None:
    if os.geteuid() != 0:
        print("This script must be run as root. Please use: sudo", sys.argv[0], "...")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Simulate EC2-style inter-zone latencies on a Linux host using tc."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="apply latency rules for this node")
    set_parser.add_argument("ips_file", help="CSV file with Zone,IP rows")
    set_parser.add_argument("lats_file", help="CSV latency matrix (values in ms)")
    set_parser.add_argument("iface", help="network interface name")

    unset_parser = subparsers.add_parser("unset", help="remove all latency rules")
    unset_parser.add_argument("iface", help="network interface name")

    set_local_parser = subparsers.add_parser(
        "set-local", help="apply per-port latency rules on the loopback interface"
    )
    set_local_parser.add_argument(
        "destinations_file", help="CSV file with Port,Latency rows"
    )
    set_local_parser.add_argument(
        "--protocol",
        choices=["tcp", "udp"],
        default="tcp",
        help="IP protocol to match (default: tcp)",
    )
    set_local_parser.add_argument(
        "--dst-ip",
        default="127.0.0.1",
        help="destination IP to match on lo (default: 127.0.0.1)",
    )
    lo_zones_parser = subparsers.add_parser(
        "set-lo-zones", help="several sites on one host, one loopback IP each: inter-zone matrix by source+destination IP"
    )
    lo_zones_parser.add_argument("ips_file", help="CSV file with Zone,IP rows")
    lo_zones_parser.add_argument("lats_file", help="CSV latency matrix (one-way ms)")
    lo_zones_parser.add_argument("--lan", default=None, help="CSV with IP,Port,Latency rows for traffic inside a site")
    lo_zones_parser.add_argument("--lan-jitter-pct", type=float, default=5.0, help="jitter of the LAN rules (default: 5)")
    for p in (set_parser, set_local_parser, lo_zones_parser):
        p.add_argument(
            "--jitter-pct",
            type=float,
            default=5.0,
            help="jitter as %% of the latency, normal distribution; 0 = fixed delay, no reordering (default: 5)",
        )
        p.add_argument(
            "--limit",
            type=int,
            default=None,
            help="netem queue limit in packets; must exceed packet rate x delay (default: netem's 1000)",
        )

    args = parser.parse_args()

    if args.command == "unset":
        iface = args.iface
        print(f"# Removing tc rules for {iface}")
        run_command(["tc", "qdisc", "del", "dev", iface, "root"], ignore_errors=True)
        return

    if args.command == "set-local":
        set_local(args.destinations_file, args.protocol, args.dst_ip, args.jitter_pct, args.limit)
        return

    if args.command == "set-lo-zones":
        set_lo_zones(args.ips_file, args.lats_file, args.lan, args.jitter_pct, args.limit, args.lan_jitter_pct)
        return

    ips_file = args.ips_file
    lats_file = args.lats_file
    iface = args.iface

    with open(ips_file, "r") as f:
        ip_data: list[dict[str, str]] = list(csv.DictReader(f))

    # Warn about duplicate IPs; first row wins.
    seen_ips: set[str] = set()
    unique_ip_data: list[dict[str, str]] = []
    for item in ip_data:
        ip = item["IP"]
        if ip in seen_ips:
            print(
                f"# warning: IP {ip} appears in more than one row of {ips_file}; keeping the first",
                file=sys.stderr,
            )
            continue
        seen_ips.add(ip)
        unique_ip_data.append(item)
    ip_data = unique_ip_data

    try:
        my_ip = get_iface_ipv4(iface)
    except ValueError as e:
        print(e)
        sys.exit(1)

    with open(lats_file, "r") as f:
        rows: list[list[str]] = list(csv.reader(f))

    zones: list[str] = rows[0]
    header_zones: set[str] = set(zones[1:])

    # Warn about duplicate zone columns in the header.
    seen_zones: set[str] = set()
    for zone in zones[1:]:
        if zone in seen_zones:
            print(
                f"# warning: duplicate zone column {zone} in latency matrix header",
                file=sys.stderr,
            )
        seen_zones.add(zone)

    # Warn about header zones with no matching matrix row.
    row_zones: set[str] = {row[0] for row in rows[1:] if row}
    for zone in zones[1:]:
        if zone not in row_zones:
            print(
                f"# warning: zone {zone} in header has no matching matrix row; skipping",
                file=sys.stderr,
            )

    # Warn about matrix rows whose zone is not in the header.
    for row in rows[1:]:
        if row and row[0] not in header_zones:
            print(
                f"# warning: matrix row for zone {row[0]} not in header; data unused",
                file=sys.stderr,
            )

    # Find my zone
    my_zone = next((item["Zone"] for item in ip_data if item["IP"] == my_ip), "")
    if not my_zone:
        print(f"IP {my_ip} not in IP file")
        sys.exit(1)

    # Map latencies
    lat_map: dict[tuple[str, str], float] = {}
    warned_cells: set[tuple[str, str]] = set()
    for row in rows[1:]:
        row_zone = row[0]
        for i in range(1, len(zones)):
            col_zone = zones[i]
            try:
                lat_map[(row_zone, col_zone)] = float(row[i])
            except (ValueError, IndexError):
                if (row_zone, col_zone) not in warned_cells:
                    print(
                        f"# warning: empty or invalid latency for {row_zone} -> {col_zone}",
                        file=sys.stderr,
                    )
                    warned_cells.add((row_zone, col_zone))
                continue

    # Warn about zones in the matrix with no IP rows in ips.csv.
    ip_zones: set[str] = {item["Zone"] for item in ip_data}
    for zone in zones[1:]:
        if zone not in ip_zones:
            print(
                f"# warning: zone {zone} has no IP rows in {ips_file}; rules will match nothing",
                file=sys.stderr,
            )

    # Warn about ips.csv zones not present in the matrix header.
    for item in ip_data:
        if item["Zone"] not in header_zones:
            print(
                f"# warning: zone {item['Zone']} in {ips_file} not in latency matrix header; no rules will apply",
                file=sys.stderr,
            )

    print(f"# Setting rules for interface {iface} in zone {my_zone} with IP {my_ip}")

    setup_root_qdisc(iface, RATE)

    next_handle = 11
    for az in zones[1:]:
        lat = lat_map.get((my_zone, az))

        # Apply rules if latency > 0
        if lat is not None and lat > 0:
            delta = args.jitter_pct / 100 * lat
            print(f"# Setting latency to {lat}ms for zone {az}")

            # Create class for this zone
            run_command(
                [
                    "tc",
                    "class",
                    "add",
                    "dev",
                    iface,
                    "parent",
                    "1:1",
                    "classid",
                    f"1:{next_handle}",
                    "htb",
                    "rate",
                    RATE,
                ]
            )

            # Apply netem delay
            run_command(
                [
                    "tc",
                    "qdisc",
                    "add",
                    "dev",
                    iface,
                    "parent",
                    f"1:{next_handle}",
                    "handle",
                    f"{next_handle}:",
                    *netem_args(lat, args.jitter_pct, args.limit),
                ]
            )

            # Add filters for IPs in this zone
            for item in ip_data:
                if item["Zone"] == az:
                    target_ip = item["IP"]
                    print(
                        f"\t# Latency from {my_ip} to {target_ip} set to {lat} +/- {delta}"
                    )
                    run_command(
                        [
                            "tc",
                            "filter",
                            "add",
                            "dev",
                            iface,
                            "protocol",
                            "ip",
                            "parent",
                            "1:",
                            "prio",
                            "1",
                            "u32",
                            "match",
                            "ip",
                            "dst",
                            f"{target_ip}/32",
                            "flowid",
                            f"1:{next_handle}",
                        ]
                    )

            next_handle += 1


if __name__ == "__main__":
    main()
