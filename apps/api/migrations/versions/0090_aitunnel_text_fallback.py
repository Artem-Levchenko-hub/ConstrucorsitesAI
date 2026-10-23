"""Keep text fallback expenses and refused attempts explicitly provider scoped."""

import sqlalchemy as sa
from alembic import op

revision = "0090_aitunnel_text_fallback"
down_revision = "0089_shared_max_bots"
branch_labels = None
depends_on = None


def _replace_checks(*, fallback: bool) -> None:
    scope = "provider_scope IN ('llmgw','aitunnel')" if fallback else "provider_scope = 'llmgw'"
    reasons = "'completed','preconnect','rate_limit_refused'"
    if fallback:
        reasons += ",'upstream_refused'"
    op.drop_constraint("ck_provider_calls_scope", "provider_calls", type_="check")
    op.create_check_constraint("ck_provider_calls_scope", "provider_calls", scope)
    op.drop_constraint("ck_generation_attempt_terminal", "generation_provider_attempts", type_="check")
    op.create_check_constraint("ck_generation_attempt_terminal", "generation_provider_attempts",
        "terminal_reason IS NULL OR (phase='closed' AND terminal_reason IN (" + reasons + "))")


def upgrade() -> None:
    _replace_checks(fallback=True)


def downgrade() -> None:
    # Both writers must be excluded before inspecting evidence. A rollback
    # never relabels provider expenses or deletes control/financial receipts.
    op.execute("LOCK TABLE provider_calls, generation_provider_attempts IN ACCESS EXCLUSIVE MODE")
    present = op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM provider_calls WHERE provider_scope='aitunnel') OR "
        "EXISTS(SELECT 1 FROM generation_provider_attempts WHERE terminal_reason='upstream_refused')"
    ))
    if present:
        raise RuntimeError("aitunnel_fallback_evidence_present: downgrade requires explicit resolution")
    _replace_checks(fallback=False)
