from __future__ import annotations

import pytest

from yleum_api.services.generation.response_capacity import capacity_values


def info(**drive):
    return {
        "servers": [
            {
                "state": "online",
                "drives": [
                    {
                        "state": "ok",
                        "totalspace": 10000,
                        "usedspace": 4000,
                        "availspace": 6000,
                        **drive,
                    }
                ],
            }
        ],
        "minio_env_vars": {"secret": "never-forward"},
    }


def test_actual_server_drives_shape_returns_only_capacity_integers():
    assert capacity_values(info(), {"quota": 8192, "quotatype": "hard"}) == {
        "quota_bytes": 8192,
        "free_bytes": 6000,
    }


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"servers": []},
        {"servers": [{"state": "ok", "disks": []}]},
        info(state="offline"),
        info(availspace=True),
        info(availspace=-1),
        info(availspace=10001),
    ],
)
def test_unknown_or_unhealthy_capacity_fails_closed(document):
    with pytest.raises(ValueError):
        capacity_values(document, {"quota": 8192, "quotatype": "hard"})


@pytest.mark.parametrize(
    "quota",
    [
        {},
        {"quota": 0, "quotatype": "hard"},
        {"quota": True, "quotatype": "hard"},
        {"quota": 8192, "quotatype": "fifo"},
    ],
)
def test_unknown_quota_never_admits(quota):
    with pytest.raises(ValueError):
        capacity_values(info(), quota)


async def test_owner_failure_precedes_any_admin_capacity_access(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import UUID

    from yleum_api.services.generation import response_capacity as service

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            generation_response_retention_enabled=True,
            generation_response_storage_epoch=str(UUID(int=9)),
            generation_response_storage_budget_bytes=10000,
            generation_response_capacity_min_free_bytes=1000,
        ),
    )
    monkeypatch.setattr(
        service, "validate_capacity_identity", AsyncMock(side_effect=ValueError("foreign owner"))
    )
    admin = Mock(side_effect=AssertionError("owner denied before privileged access"))
    monkeypatch.setattr(service, "_admin_capacity", admin)
    with pytest.raises(ValueError, match="foreign owner"):
        await service.current_capacity(object())
    admin.assert_not_called()
