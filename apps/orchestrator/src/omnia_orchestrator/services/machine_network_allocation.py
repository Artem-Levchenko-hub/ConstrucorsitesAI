"""Dedicated controller-configured /28 allocation without changing Docker pools."""

import hashlib
import ipaddress

import docker


def choose_subnet(pool: str, occupied: list[str], key: str) -> str:
    network = ipaddress.ip_network(pool, strict=True)
    if (
        network.version != 4
        or network.prefixlen > 28
        or network.prefixlen < 16
        or not any(
            network.subnet_of(ipaddress.ip_network(private))
            for private in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        )
    ):
        raise ValueError("cell pool must be a private IPv4 /16 through /28 network")
    candidates = list(network.subnets(new_prefix=28))
    unavailable = [ipaddress.ip_network(value, strict=False) for value in occupied]
    start = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(candidates)
    for index in range(len(candidates)):
        candidate = candidates[(start + index) % len(candidates)]
        if not any(candidate.overlaps(other) for other in unavailable if other.version == 4):
            return str(candidate)
    raise ValueError("configured cell subnet pool exhausted or overlaps reserved routes")


def docker_network_subnets(client) -> list[str]:
    return [
        entry["Subnet"]
        for network in client.networks.list()
        for entry in (network.attrs.get("IPAM", {}).get("Config") or [])
        if entry.get("Subnet")
    ]


def create_pool_network(client, pool: str, name: str, **options):
    """Docker is the cross-controller allocation arbiter; refresh after a race."""
    for attempt in range(8):
        subnet = choose_subnet(pool, docker_network_subnets(client), name)
        try:
            return client.networks.create(
                name,
                **options,
                ipam=docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet=subnet)]),
            )
        except docker.errors.APIError as exc:
            explanation = (str(exc) + " " + str(exc.explanation)).lower()
            if attempt == 7 or "pool overlaps" not in explanation:
                raise
    raise AssertionError("unreachable allocation loop")
