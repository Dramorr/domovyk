"""Комуналка: постачальники, лічильники, показники (FR-UTL-1..5, M5 частина 3)

Постачальники й вікна подачі — дані з прив'язкою до міста (FR-CTY-4), не код.
Лічильник належить об'єкту; показник — лічильнику, з прив'язкою до оренди, в якій
його подали. До нарахувань додано примітку (розбивка комуналки для орендаря).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-11
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TYPE meter_kind AS ENUM ('electricity', 'water_cold', 'water_hot', 'gas', 'heat');

CREATE TABLE utility_providers (
    id              serial PRIMARY KEY,
    code            text NOT NULL UNIQUE CHECK (code ~ '^[a-z][a-z0-9_]{1,40}$'),
    name            text NOT NULL,
    meter_kinds     meter_kind[] NOT NULL,
    window_rule     jsonb NOT NULL DEFAULT '{}',
    cabinet_url     text CHECK (cabinet_url IS NULL OR cabinet_url ~ '^https://'),
    payment_url     text CHECK (payment_url IS NULL OR payment_url ~ '^https://'),
    city_code       text NOT NULL REFERENCES cities(code)
);
CREATE INDEX idx_utility_providers_city ON utility_providers(city_code);

-- Вікна подачі показників — станом на листопад 2026, перевіряти перед запуском.
INSERT INTO utility_providers (code, name, meter_kinds, window_rule, cabinet_url, city_code) VALUES
    ('lviv_vodokanal', 'ЛМКП «Львівводоканал»', '{water_cold}',
        '{"from_day": 10, "to": "last_working_day"}', 'https://infolviv.com.ua', 'lviv'),
    ('lviv_teploenergo', 'ЛМКП «Львівтеплоенерго»', '{water_hot,heat}',
        '{"from_day": 10, "to": "last_working_day"}', 'https://lte.leotel.com.ua', 'lviv'),
    ('lviv_energozbut', '«Львівенергозбут»', '{electricity}',
        '{"from_days_before_month_end": 2, "to_day_next_month": 3}', 'https://lez.com.ua', 'lviv'),
    ('gazmerezhi_lviv', '«Газмережі», Львівська філія', '{gas}',
        '{"from_day": 1, "to_day": 5}', 'https://my.grmu.com.ua', 'lviv');

CREATE TABLE meters (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id         uuid NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    kind                meter_kind NOT NULL,
    provider_id         int REFERENCES utility_providers(id),
    account_number      text CHECK (account_number IS NULL OR char_length(account_number) <= 40),
    serial_number       text CHECK (serial_number IS NULL OR char_length(serial_number) <= 40),
    label               text CHECK (label IS NULL OR char_length(label) <= 40),
    submits             party_role NOT NULL DEFAULT 'tenant',
    pays                party_role NOT NULL DEFAULT 'tenant',
    tariff              numeric(10,4) CHECK (tariff IS NULL OR tariff > 0),
    is_active           boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_meters_property ON meters(property_id);

CREATE TABLE meter_readings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meter_id        uuid NOT NULL REFERENCES meters(id) ON DELETE CASCADE,
    lease_id        uuid REFERENCES leases(id) ON DELETE SET NULL,
    value           numeric(12,3) NOT NULL CHECK (value >= 0),
    photo_key       text,
    submitted_by    uuid NOT NULL REFERENCES users(id),
    reading_date    date NOT NULL,
    sent_to_provider_at timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_meter_readings_meter
    ON meter_readings(meter_id, reading_date DESC, created_at DESC);

ALTER TABLE rent_charges ADD COLUMN note text
    CHECK (note IS NULL OR char_length(note) <= 300);
"""

DOWNGRADE = """
ALTER TABLE rent_charges DROP COLUMN note;
DROP TABLE meter_readings;
DROP TABLE meters;
DROP TABLE utility_providers;
DROP TYPE meter_kind;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
