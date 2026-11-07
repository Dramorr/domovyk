"""Скасовані оренди: «видалити зі списку» і повне очищення (FR-LSE-9)

Кожен учасник прибирає скасовану оренду лише у себе (`lease_hidden`). Коли прибрали
всі або минуло LEASE_CANCELLED_RETENTION_DAYS днів після скасування — оренда
видаляється повністю (каскадом: учасники, запрошення, чат, договори, підписи).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TABLE lease_hidden (
    lease_id    uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users(id),
    hidden_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lease_id, user_id)
);
CREATE INDEX idx_leases_cancelled ON leases(cancelled_at) WHERE status = 'cancelled';
"""

DOWNGRADE = """
DROP INDEX idx_leases_cancelled;
DROP TABLE lease_hidden;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
