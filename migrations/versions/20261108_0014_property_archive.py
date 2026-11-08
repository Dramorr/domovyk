"""Архів об'єктів (FR-PRP-9)

Об'єкт із завершеною орендою не стирається: оренда посилається на його юніт, а її
історія (договір, оплати) потрібна учасникам. Такий об'єкт архівується — зникає у
власника, а адресу можна знову додати як новий об'єкт.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE properties ADD COLUMN archived_at timestamptz;
DROP INDEX uq_property_address;
CREATE UNIQUE INDEX uq_property_address
    ON properties(building_id, COALESCE(apartment_number_norm, ''))
    WHERE archived_at IS NULL;
"""

DOWNGRADE = """
DELETE FROM properties WHERE archived_at IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM units u JOIN leases l ON l.unit_id = u.id
                    WHERE u.property_id = properties.id);
DROP INDEX uq_property_address;
CREATE UNIQUE INDEX uq_property_address
    ON properties(building_id, COALESCE(apartment_number_norm, ''));
ALTER TABLE properties DROP COLUMN archived_at;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
