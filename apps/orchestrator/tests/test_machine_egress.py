import importlib
import importlib.util

import pytest


def module():
    name = "omnia_orchestrator.services.machine_egress"
    assert importlib.util.find_spec(name) is not None, "enforced machine egress is missing"
    return importlib.import_module(name)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.1",
        "172.17.0.1",
        "192.168.1.1",
        "0.0.0.0",
        "255.255.255.255",
        "224.0.0.1",
        "100.64.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:8.8.8.8",
        "2002:7f00:1::",
        "bad",
    ],
)
def test_private_and_transition_destinations_are_denied(address):
    assert module().public_destination(address) is False


@pytest.mark.parametrize("address", ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_destinations_are_allowed(address):
    assert module().public_destination(address) is True


def test_host_public_and_platform_addresses_can_never_be_reached():
    assert module().public_destination("8.8.8.8", ["8.8.8.0/24"]) is False


def test_dns_rebinding_and_mixed_answers_fail_closed():
    egress = module()
    with pytest.raises(egress.DestinationDenied):
        egress.checked_addresses(["8.8.8.8", "127.0.0.1"], [])
    assert egress.checked_addresses(["1.1.1.1"], []) == ["1.1.1.1"]
    with pytest.raises(egress.DestinationDenied):
        egress.checked_addresses([], [])


def test_guard_policy_is_identity_bound_and_only_allows_explicit_ports():
    egress = module()
    first = egress.GuardPolicy(
        workspace_id="one", proxy_ip="10.253.240.2", data_endpoints=(("10.253.240.3", 5432),)
    )
    second = egress.GuardPolicy(
        workspace_id="two", proxy_ip="10.253.240.2", data_endpoints=(("10.253.240.3", 5432),)
    )
    assert first.digest() != second.digest()
    assert first.allows("10.253.240.2", 3128)
    assert first.allows("10.253.240.3", 5432)
    assert not first.allows("10.253.240.2", 80)
    assert not first.allows("10.253.240.3", 22)
    assert not first.allows("8.8.8.8", 443)


def test_proxy_authority_rejects_credentials_and_ambiguous_destinations():
    egress = module()
    assert egress.parse_authority("pypi.org:443", 443) == ("pypi.org", 443)
    for value in (
        "user:secret@pypi.org:443",
        "pypi.org:0",
        "pypi.org:65536",
        "pypi.org\r\nHost: evil",
        "pypi.org/path",
        "127.0.0.1:22",
    ):
        with pytest.raises(egress.DestinationDenied):
            egress.parse_authority(value, 443)
