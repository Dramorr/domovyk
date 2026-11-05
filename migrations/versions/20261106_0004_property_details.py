"""Анкета об'єкта (FR-PRP-8, ADR 0003): properties.details jsonb

Анкета ще не фінальна, тому зберігаємо її одним jsonb, перевіреним моделлю
PropertyDetails (з полем "v" — версією анкети). Поля, за якими шукатимуть (M6),
винесемо в колонки або індекси, коли пошук стане зрозумілим.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE properties ADD COLUMN details jsonb NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE properties DROP COLUMN details")
