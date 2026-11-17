"""Пошук для орендаря: обране й повний список районів Львова (FR-SRC-1..6, M6 частина 2)

Райони з'являлись лише з адрес, які знайшов геокодер (4 з 6). Для фільтра потрібні всі
адміністративні райони міста — назви як у Nominatim («… район»), щоб нові адреси
потрапляли в ті самі рядки.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
INSERT INTO districts (city_code, name) VALUES
    ('lviv', 'Галицький район'), ('lviv', 'Залізничний район'),
    ('lviv', 'Личаківський район'), ('lviv', 'Сихівський район'),
    ('lviv', 'Франківський район'), ('lviv', 'Шевченківський район')
ON CONFLICT (city_code, name) DO NOTHING;

CREATE TABLE favorites (
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    listing_id  uuid NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, listing_id)
);
CREATE INDEX idx_favorites_user ON favorites(user_id, created_at DESC);
"""

DOWNGRADE = """
DROP TABLE favorites;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
