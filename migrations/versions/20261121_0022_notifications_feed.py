"""Стрічка сповіщень у Mini App (дзвіночок): індекс для вибірки за людиною

notifications.read_at уже є; стрічка бере доставлені сповіщення людини,
найновіші першими, і рахує непрочитані.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX idx_notifications_feed ON notifications(user_id, sent_at DESC) "
        "WHERE sent_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_notifications_feed")
