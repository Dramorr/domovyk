"""Розірвання й завершення оренди (FR-LSE-7)

- termination_date / termination_reason / termination_by / termination_requested_at —
  хто, коли й чому ініціював розірвання; у дату розірвання оренда стає ended;
- CHECK: дата розірвання не пізніше кінця договору.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE leases ADD COLUMN termination_date date;
ALTER TABLE leases ADD COLUMN termination_reason text
    CHECK (termination_reason IS NULL OR char_length(termination_reason) BETWEEN 3 AND 500);
ALTER TABLE leases ADD COLUMN termination_by uuid REFERENCES users(id);
ALTER TABLE leases ADD COLUMN termination_requested_at timestamptz;
ALTER TABLE leases ADD CONSTRAINT leases_termination_within_term
    CHECK (termination_date IS NULL OR termination_date <= end_date);
CREATE INDEX idx_leases_finishing ON leases(status) WHERE status IN ('active', 'ending');
"""

DOWNGRADE = """
DROP INDEX idx_leases_finishing;
ALTER TABLE leases DROP CONSTRAINT leases_termination_within_term;
ALTER TABLE leases DROP COLUMN termination_requested_at;
ALTER TABLE leases DROP COLUMN termination_by;
ALTER TABLE leases DROP COLUMN termination_reason;
ALTER TABLE leases DROP COLUMN termination_date;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
