"""Збережені пошуки й сповіщення про нові оголошення (FR-SRC-3, M6 частина 3)

Фільтр зберігається як JSON тих самих полів, що й у пошуку (SearchIn), а не окремими
колонками: фільтр росте разом з анкетою, і міграція на кожне нове поле не потрібна.
Дублі сповіщень «одне оголошення — один фільтр» відсікає dedupe_key у notifications.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TABLE search_filters (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
    city_code   text NOT NULL REFERENCES cities(code),
    criteria    jsonb NOT NULL DEFAULT '{}',
    notify      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_search_filters_user ON search_filters(user_id, created_at);
CREATE INDEX idx_search_filters_notify ON search_filters(city_code) WHERE notify;
CREATE TRIGGER trg_search_filters_updated BEFORE UPDATE ON search_filters
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""

DOWNGRADE = """
DROP TABLE search_filters;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
