"""The webhook trusts only YooKassa's networks (when configured) and reads the
payment id out of both payment and refund notifications."""

from __future__ import annotations

import pytest

from omnia_api.routers.payments import webhook_object_id
from omnia_api.services import yookassa
from omnia_api.services.yookassa import (
    YOOKASSA_NOTIFICATION_NETWORKS,
    notification_source_allowed,
)

OFFICIAL = ",".join(YOOKASSA_NOTIFICATION_NETWORKS)


def test_documented_networks_are_the_ones_from_the_provider_docs() -> None:
    assert set(YOOKASSA_NOTIFICATION_NETWORKS) == {
        "185.71.76.0/27",
        "185.71.77.0/27",
        "77.75.153.0/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "77.75.154.128/25",
        "2a02:5180::/32",
    }


@pytest.mark.parametrize(
    ("source", "allowed"),
    [
        ("185.71.76.5", True),
        ("185.71.77.31", True),
        ("77.75.153.100", True),
        ("77.75.156.11", True),
        ("77.75.156.35", True),
        ("77.75.154.200", True),
        ("2a02:5180:0:1::10", True),
        ("77.75.156.12", False),
        ("185.71.78.1", False),
        ("10.0.0.1", False),
        ("127.0.0.1", False),
        ("", False),
        (None, False),
        ("not-an-ip", False),
    ],
)
def test_official_list_accepts_only_provider_addresses(source: str | None, allowed: bool) -> None:
    assert notification_source_allowed(source, allowed_cidrs=OFFICIAL) is allowed


def test_empty_allow_list_disables_the_filter() -> None:
    assert notification_source_allowed("10.0.0.1", allowed_cidrs="") is True
    assert notification_source_allowed(None, allowed_cidrs="   ") is True


def test_custom_allow_list_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    class Settings:
        yookassa_webhook_allowed_cidrs = " 203.0.113.0/24 ,198.51.100.7 "

    monkeypatch.setattr(yookassa, "get_settings", lambda: Settings())
    assert notification_source_allowed("203.0.113.9") is True
    assert notification_source_allowed("198.51.100.7") is True
    assert notification_source_allowed("198.51.100.8") is False


def test_webhook_object_id_reads_payment_and_refund_events() -> None:
    assert webhook_object_id({"event": "payment.succeeded", "object": {"id": "p-1"}}) == "p-1"
    assert webhook_object_id({"object": {"id": "p-2"}}) == "p-2"
    assert (
        webhook_object_id(
            {"event": "refund.succeeded", "object": {"id": "r-1", "payment_id": "p-3"}}
        )
        == "p-3"
    )
    assert webhook_object_id({"event": "refund.succeeded", "object": {"id": "r-1"}}) == ""
    assert webhook_object_id({"object": "p-4"}) == ""
    assert webhook_object_id([]) == ""
    assert webhook_object_id(None) == ""
