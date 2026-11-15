"""Retain evidence of an expired attempt fenced before provider dispatch."""

import sqlalchemy as sa
from alembic import op

revision = "0107_expired_provider_reservation"
down_revision = "0106_native_transcript_refusal"
branch_labels = None
depends_on = None

_TABLE = "generation_provider_attempts"
_FINANCIAL = "'completed','preconnect','rate_limit_refused','upstream_refused'"
_NATIVE_REFUSAL = "'native_transcript_rejected'"
_EXPIRY = "'expired_before_dispatch'"


def _replace_checks(local_reasons: str) -> None:
    local = f"COALESCE(terminal_reason IN ({local_reasons}), false)"
    op.drop_constraint("ck_generation_attempt_call", _TABLE, type_="check")
    op.drop_constraint("ck_generation_attempt_terminal", _TABLE, type_="check")
    op.create_check_constraint(
        "ck_generation_attempt_call", _TABLE,
        f"(phase='reserved' OR (phase='closed' AND {local})) = (provider_call_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_generation_attempt_terminal", _TABLE,
        "terminal_reason IS NULL OR (phase='closed' AND "
        f"(terminal_reason IN ({_FINANCIAL}) OR ({local} "
        "AND response_manifest_key IS NULL AND response_sha256 IS NULL)))",
    )


def upgrade() -> None:
    _replace_checks(f"{_NATIVE_REFUSAL},{_EXPIRY}")


def downgrade() -> None:
    op.execute("LOCK TABLE generation_provider_attempts IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM generation_provider_attempts "
        f"WHERE terminal_reason={_EXPIRY})",
    )):
        raise RuntimeError("expired_provider_reservation_evidence_present")
    _replace_checks(_NATIVE_REFUSAL)
