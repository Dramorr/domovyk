"""Довідник міст: Домовик готовий до інших міст, крім Львова (ADR 0008)

Місто — рядок довідника `cities` (код латиницею, назва, статус), а не текст «Львів»
у кожному будинку. Будинки й райони посилаються на `cities.code`; ключ будинку
починається з коду міста ('lviv|вул шевченка|12а').

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TABLE cities (
    code        text PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9_]{1,30}$'),
    name        text NOT NULL UNIQUE,
    region      text NOT NULL,
    status      text NOT NULL DEFAULT 'soon' CHECK (status IN ('active', 'soon')),
    timezone    text NOT NULL DEFAULT 'Europe/Kyiv',
    sort_order  smallint NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO cities (code, name, region, status, sort_order)
VALUES ('lviv', 'Львів', 'Львівська область', 'active', 1);

ALTER TABLE districts ADD COLUMN city_code text REFERENCES cities(code);
UPDATE districts SET city_code = 'lviv';
ALTER TABLE districts ALTER COLUMN city_code SET NOT NULL;
ALTER TABLE districts DROP CONSTRAINT districts_city_name_key;
ALTER TABLE districts DROP COLUMN city;
ALTER TABLE districts ADD CONSTRAINT districts_city_code_name_key UNIQUE (city_code, name);

ALTER TABLE buildings ADD COLUMN city_code text REFERENCES cities(code);
UPDATE buildings SET city_code = 'lviv',
    normalized_key = regexp_replace(normalized_key, '^[^|]*', 'lviv');
ALTER TABLE buildings ALTER COLUMN city_code SET NOT NULL;
ALTER TABLE buildings DROP COLUMN city;
CREATE INDEX idx_buildings_city ON buildings(city_code);
"""

DOWNGRADE = """
DROP INDEX idx_buildings_city;
ALTER TABLE buildings ADD COLUMN city text NOT NULL DEFAULT 'Львів';
UPDATE buildings SET normalized_key = regexp_replace(normalized_key, '^[^|]*', 'львів');
ALTER TABLE buildings DROP COLUMN city_code;

ALTER TABLE districts DROP CONSTRAINT districts_city_code_name_key;
ALTER TABLE districts ADD COLUMN city text NOT NULL DEFAULT 'Львів';
ALTER TABLE districts DROP COLUMN city_code;
ALTER TABLE districts ADD CONSTRAINT districts_city_name_key UNIQUE (city, name);

DROP TABLE cities;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
