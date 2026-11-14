"""Close authenticated native requests rejected before any provider dispatch."""

import sqlalchemy as sa
from alembic import op

revision = "0106_native_transcript_refusal"
down_revision = "0105_max_nutrition_ai"
branch_labels = None
depends_on = None

_TABLE = "generation_provider_attempts"
_FINANCIAL_REASONS = "'completed','preconnect','rate_limit_refused','upstream_refused'"
_LOCAL_REFUSAL = "terminal_reason IS NOT DISTINCT FROM 'native_transcript_rejected'"


def _replace_attempt_checks(*, local_refusal: bool) -> None:
    reasons = (
        f"terminal_reason IN ({_FINANCIAL_REASONS})"
        + (f" OR ({_LOCAL_REFUSAL} AND response_manifest_key IS NULL AND response_sha256 IS NULL)"
           if local_refusal else "")
    )
    call_check = (
        f"(phase='reserved' OR (phase='closed' AND {_LOCAL_REFUSAL})) = (provider_call_id IS NULL)"
        if local_refusal else
        "(phase='reserved' AND provider_call_id IS NULL) OR "
        "(phase<>'reserved' AND provider_call_id IS NOT NULL)"
    )
    op.drop_constraint("ck_generation_attempt_call", _TABLE, type_="check")
    op.drop_constraint("ck_generation_attempt_terminal", _TABLE, type_="check")
    op.create_check_constraint("ck_generation_attempt_call", _TABLE, call_check)
    op.create_check_constraint(
        "ck_generation_attempt_terminal", _TABLE,
        f"terminal_reason IS NULL OR (phase='closed' AND ({reasons}))",
    )


def upgrade() -> None:
    _replace_attempt_checks(local_refusal=True)


def downgrade() -> None:
    op.execute("LOCK TABLE generation_provider_attempts IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM generation_provider_attempts WHERE " + _LOCAL_REFUSAL + ")"
    )):
        raise RuntimeError("native_transcript_refusal_evidence_present: downgrade would discard dispatch control")
    _replace_attempt_checks(local_refusal=False)
