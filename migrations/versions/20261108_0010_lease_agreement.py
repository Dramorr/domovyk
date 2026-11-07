"""Погодження умов обома сторонами і пропозиції змін (FR-LSE-10)

- landlord_accepted_at — власник погодив поточні умови (явно або тим, що сам їх задав);
- proposal / proposal_by / proposal_at — запропоновані орендарем зміни (без реквізитів);
- declined_by / declined_at — хто відхилив останню пропозицію (жовта половина смужки).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE leases ADD COLUMN landlord_accepted_at timestamptz;
ALTER TABLE leases ADD COLUMN proposal jsonb;
ALTER TABLE leases ADD COLUMN proposal_by uuid REFERENCES users(id);
ALTER TABLE leases ADD COLUMN proposal_at timestamptz;
ALTER TABLE leases ADD COLUMN declined_by uuid REFERENCES users(id);
ALTER TABLE leases ADD COLUMN declined_at timestamptz;
-- Наявні оренди: умови задавав власник — вважаємо, що він їх погодив.
UPDATE leases SET landlord_accepted_at = created_at WHERE status <> 'cancelled';
"""

DOWNGRADE = """
ALTER TABLE leases DROP COLUMN declined_at;
ALTER TABLE leases DROP COLUMN declined_by;
ALTER TABLE leases DROP COLUMN proposal_at;
ALTER TABLE leases DROP COLUMN proposal_by;
ALTER TABLE leases DROP COLUMN proposal;
ALTER TABLE leases DROP COLUMN landlord_accepted_at;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
