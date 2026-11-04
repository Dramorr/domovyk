"""baseline: розширення PostGIS (db/schema.sql, рядок CREATE EXTENSION)

Таблиці з'являються наступними міграціями, по мірі етапів M1–M8.

Revision ID: 0001
Revises:
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    # Свідомо не видаляємо PostGIS: від нього можуть залежати інші розширення
    # (postgis_topology, tiger у docker-образі) і дані поза нашими таблицями.
    pass
