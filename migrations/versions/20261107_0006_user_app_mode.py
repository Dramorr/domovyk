"""Режим застосунку користувача: орендар чи власник (ADR 0006)

NULL — ще не обрав (крок ONB-4 після реєстрації). Це не тип акаунта й не роль:
ролі визначаються в конкретній оренді чи об'єкті.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN app_mode text CHECK (app_mode IN ('tenant', 'landlord'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN app_mode")
