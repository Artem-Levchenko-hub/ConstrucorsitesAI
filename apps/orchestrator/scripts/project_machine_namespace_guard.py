"""Trusted guard PID 1; runs only inside its own Docker network namespace.

Project processes share this network namespace but have no NET_ADMIN/NET_RAW.
No host paths, Docker socket, generated source, or secrets are mounted here.
"""

import argparse
import hashlib
import ipaddress
import json
import signal
import subprocess


def install_policy(policy):
    proxy = str(ipaddress.IPv4Address(policy["proxy_ip"]))
    destinations = [(proxy, 3128)] + [
        (str(ipaddress.IPv4Address(address)), int(port))
        for address, port in policy["data_endpoints"]
    ]
    for _address, port in destinations:
        if not 0 < port <= 65535:
            raise ValueError("invalid port")
    # Policy applies to OUTPUT too: Docker's loopback DNS is not an escape hatch.
    for binary in ("iptables", "ip6tables"):
        subprocess.run([binary, "-P", "INPUT", "DROP"], check=True)
        subprocess.run([binary, "-P", "FORWARD", "DROP"], check=True)
        subprocess.run([binary, "-P", "OUTPUT", "DROP"], check=True)
        for chain in ("INPUT", "OUTPUT"):
            subprocess.run(
                [
                    binary,
                    "-A",
                    chain,
                    "-m",
                    "conntrack",
                    "--ctstate",
                    "ESTABLISHED,RELATED",
                    "-j",
                    "ACCEPT",
                ],
                check=True,
            )
    # Only literal 127.0.0.1, not all loopback: 127.0.0.11 is Docker DNS.
    for chain in ("INPUT", "OUTPUT"):
        subprocess.run(
            ["iptables", "-A", chain, "-s", "127.0.0.1", "-d", "127.0.0.1", "-j", "ACCEPT"],
            check=True,
        )
    for address, port in destinations:
        subprocess.run(
            [
                "iptables",
                "-A",
                "OUTPUT",
                "-d",
                address,
                "-p",
                "tcp",
                "--dport",
                str(port),
                "-j",
                "ACCEPT",
            ],
            check=True,
        )
    # Host-side HTTP verification/preview may connect to project services. Replies
    # are covered by conntrack; this does not authorize a new project-to-host flow.
    subprocess.run(["iptables", "-A", "INPUT", "-p", "tcp", "-j", "ACCEPT"], check=True)
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    print("POLICY_READY=" + digest, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("policy")
    args = parser.parse_args()
    install_policy(json.loads(args.policy))
    while True:
        signal.pause()
