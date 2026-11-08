"""Фото об'єкта (FR-PRP-9, ADR 0011)

Фото належать об'єкту, а не оголошенню: їх бачать в анкеті й у картці оренди,
а оголошення (M6) показуватиме ці самі фото. Файл — JPEG без EXIF у сховищі.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TABLE property_photos (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id     uuid NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    storage_key     text NOT NULL UNIQUE,
    position        smallint NOT NULL DEFAULT 0 CHECK (position >= 0),
    width           int NOT NULL CHECK (width > 0),
    height          int NOT NULL CHECK (height > 0),
    phash           bigint,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_property_photos_property ON property_photos(property_id, position);
CREATE INDEX idx_property_photos_phash ON property_photos(phash);
"""

DOWNGRADE = """
DROP TABLE property_photos;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
