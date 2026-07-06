"""Self-credit guard on POST /api/wallet/topup — pure config check, no DB.

The topup stub credits the caller's OWN wallet with no payment. The one property
that must never regress is the SECURE DEFAULT: the hole is closed unless an
operator explicitly opts in via ALLOW_STUB_TOPUP. (The full 403-vs-200 endpoint
behaviour is covered by the DB-backed suite / CI-with-Postgres.)
"""

from __future__ import annotations

from omnia_api.core.config import Settings


def test_stub_topup_is_closed_by_default() -> None:
    # A fresh deploy / test / new env must NOT allow free self-credit.
    assert Settings.model_fields["allow_stub_topup"].default is False


def test_wallet_router_gates_topup_on_the_flag() -> None:
    # The handler must consult the flag before crediting (defence against a
    # refactor that drops the guard). Assert the source references it.
    import inspect

    from omnia_api.routers import wallet

    src = inspect.getsource(wallet.topup_wallet)
    assert "allow_stub_topup" in src
    assert "403" in src
