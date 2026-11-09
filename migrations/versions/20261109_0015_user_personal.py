"""Персональні дані й аватар користувача (FR-USR-3, ADR 0016)

ПІБ (як у документах — для договору), місто з довідника, аватар (файл у сховищі).
Дата народження вже була в схемі: тепер її заповнює сам користувач, мінімум 18 років
перевіряє сервіс (поріг — у конфігурації).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-09
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE users ADD COLUMN legal_name text
    CHECK (legal_name IS NULL OR length(legal_name) BETWEEN 3 AND 150);
ALTER TABLE users ADD COLUMN city_code text REFERENCES cities(code);
ALTER TABLE users ADD COLUMN avatar_key text;
ALTER TABLE users ADD COLUMN avatar_updated_at timestamptz;
ALTER TABLE users ADD CONSTRAINT ck_users_birth_date
    CHECK (birth_date IS NULL OR birth_date >= DATE '1900-01-01');
"""

DOWNGRADE = """
ALTER TABLE users DROP CONSTRAINT ck_users_birth_date;
ALTER TABLE users DROP COLUMN avatar_updated_at;
ALTER TABLE users DROP COLUMN avatar_key;
ALTER TABLE users DROP COLUMN city_code;
ALTER TABLE users DROP COLUMN legal_name;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
