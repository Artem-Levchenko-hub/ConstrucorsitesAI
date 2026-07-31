"""Persist the canonical attestation timestamp for digest verification.

Revision ID: 0039_attestation_integrity
Revises: 0038_subscription_lifecycle
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_attestation_integrity"
down_revision = "0038_subscription_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing records intentionally remain NULL: their canonical timestamp was
    # never persisted, so their digest cannot be proven and production must
    # require a fresh verified build before the next deploy.
    op.add_column("attestations", sa.Column("issued_at", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("attestations", "issued_at")
