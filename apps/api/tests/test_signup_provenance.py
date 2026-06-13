"""V4.2b — signup provenance seam (viral return-edge, backend contract).

Falsifiable gate (CONTINUOUS-PLAN §5★ V4.2b PROVENANCE assert): a stranger who
returns via a share-link CTA registers with ``source=='share_link'`` and a
``referrer_project_id`` pointing at the SOURCE project — and that provenance is
PERSISTED on the signup row. Removing the params flips the row back to blank
(both NULL), proving the link — not a default — drives provenance. Junk
provenance (out-of-enum source / non-UUID referrer) is rejected with a 422 so
the column can never be spoofed.
"""

import uuid

import httpx
from sqlalchemy import select

from omnia_api.models.user import User


async def _source(db_session, email: str) -> tuple[str | None, uuid.UUID | None]:
    row = (
        await db_session.execute(
            select(User.signup_source, User.referrer_project_id).where(
                User.email == email
            )
        )
    ).one()
    return row[0], row[1]


async def test_share_link_signup_persists_source_and_referrer(
    client: httpx.AsyncClient, db_session
) -> None:
    ref = uuid.uuid4()
    r = await client.post(
        "/api/auth/register",
        json={
            "email": "stranger@example.com",
            "password": "secret123",
            "source": "share_link",
            "referrer_project_id": str(ref),
        },
    )
    assert r.status_code == 201, r.text
    src, referrer = await _source(db_session, "stranger@example.com")
    assert src == "share_link"
    assert referrer == ref


async def test_organic_signup_leaves_provenance_blank(
    client: httpx.AsyncClient, db_session
) -> None:
    # No source/referrer params → blank provenance. This NULL is the falsifiable
    # "the link, not a default, drives provenance" signal: if the endpoint ever
    # stamped a default source, this assert would catch it.
    r = await client.post(
        "/api/auth/register",
        json={"email": "organic@example.com", "password": "secret123"},
    )
    assert r.status_code == 201, r.text
    src, referrer = await _source(db_session, "organic@example.com")
    assert src is None
    assert referrer is None


async def test_source_only_records_source_referrer_null(
    client: httpx.AsyncClient, db_session
) -> None:
    # The two fields are independent: a source with no referrer persists the
    # source and leaves the pointer NULL (no spurious referrer is invented).
    r = await client.post(
        "/api/auth/register",
        json={
            "email": "direct@example.com",
            "password": "secret123",
            "source": "direct",
        },
    )
    assert r.status_code == 201, r.text
    src, referrer = await _source(db_session, "direct@example.com")
    assert src == "direct"
    assert referrer is None


async def test_out_of_enum_source_rejected(client: httpx.AsyncClient) -> None:
    # Teeth: an arbitrary string can NOT be smuggled into the provenance column.
    r = await client.post(
        "/api/auth/register",
        json={
            "email": "spoof@example.com",
            "password": "secret123",
            "source": "evil_value",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"


async def test_non_uuid_referrer_rejected(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/register",
        json={
            "email": "spoof2@example.com",
            "password": "secret123",
            "source": "share_link",
            "referrer_project_id": "not-a-uuid",
        },
    )
    assert r.status_code == 422
