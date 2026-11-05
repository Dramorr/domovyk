"""M2: адреси, об'єкти, юніти, верифікація власності, сигнали ризику, outbox сповіщень

Таблиці як у db/schema.sql (розділи 2, 11, 12). Відхилення від схеми v0.3
(схему оновлено в тому ж коміті, v0.4):
- verification_status має стан 'draft' — власник ще додає документи;
- user_verifications: замість одного document_key — таблиця verification_documents
  (довідка + довіреність чи згода співвласника), плюс submitted_at;
- одна відкрита (draft/pending) перевірка власності на об'єкт — частковий унікальний індекс;
- property_delegates — пізніше (FR-PRP-6, разом із показами в M3).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-05
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TYPE verification_kind   AS ENUM ('phone', 'identity', 'ownership');
CREATE TYPE verification_status AS ENUM ('draft', 'pending', 'verified', 'rejected', 'expired');
CREATE TYPE property_kind       AS ENUM ('apartment', 'house');
CREATE TYPE unit_kind           AS ENUM ('entire', 'room');
CREATE TYPE ownership_status    AS ENUM ('unverified', 'pending', 'verified', 'rejected');

CREATE TABLE districts (
    id          serial PRIMARY KEY,
    city        text NOT NULL DEFAULT 'Львів',
    name        text NOT NULL,
    parent_id   int REFERENCES districts(id),
    UNIQUE (city, name)
);

CREATE TABLE buildings (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    city                text NOT NULL DEFAULT 'Львів',
    street              text NOT NULL,
    house_number        text NOT NULL,
    normalized_key      text NOT NULL UNIQUE,
    district_id         int REFERENCES districts(id),
    location            geography(Point, 4326),
    geocoder_place_id   text,
    floors_total        smallint,
    has_backup_power    boolean,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_buildings_location ON buildings USING gist(location);
CREATE INDEX idx_buildings_district ON buildings(district_id);

CREATE TABLE properties (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id           uuid NOT NULL REFERENCES users(id),
    kind                    property_kind NOT NULL DEFAULT 'apartment',
    building_id             uuid NOT NULL REFERENCES buildings(id),
    apartment_number        text,
    apartment_number_norm   text,
    entrance                text,
    floor                   smallint,
    rooms                   smallint NOT NULL CHECK (rooms BETWEEN 0 AND 30),
    area_total              numeric(7,1) CHECK (area_total > 0),
    ownership_status        ownership_status NOT NULL DEFAULT 'unverified',
    ownership_verified_at   timestamptz,
    settings                jsonb NOT NULL DEFAULT '{}',
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CHECK (kind = 'house' OR apartment_number_norm IS NOT NULL)
);
CREATE UNIQUE INDEX uq_property_address
    ON properties(building_id, COALESCE(apartment_number_norm, ''));
CREATE INDEX idx_properties_owner ON properties(owner_user_id);
CREATE TRIGGER trg_properties_updated BEFORE UPDATE ON properties
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE units (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id     uuid NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    kind            unit_kind NOT NULL DEFAULT 'entire',
    name            text NOT NULL,
    area            numeric(7,1),
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_one_entire_unit ON units(property_id) WHERE kind = 'entire';
CREATE INDEX idx_units_property ON units(property_id);

CREATE TABLE user_verifications (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            verification_kind   NOT NULL,
    status          verification_status NOT NULL DEFAULT 'pending',
    provider        identity_provider,
    identity_hash   text,
    property_id     uuid REFERENCES properties(id) ON DELETE CASCADE,
    owner_name_in_doc text,
    is_dev_bypass   boolean NOT NULL DEFAULT false,
    reviewed_by     uuid REFERENCES users(id),
    reject_reason   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    submitted_at    timestamptz,
    decided_at      timestamptz,
    CHECK (kind <> 'ownership' OR property_id IS NOT NULL)
);
CREATE UNIQUE INDEX uq_verified_identity
    ON user_verifications(identity_hash)
    WHERE kind = 'identity' AND status = 'verified';
CREATE UNIQUE INDEX uq_open_ownership_verification
    ON user_verifications(property_id)
    WHERE kind = 'ownership' AND status IN ('draft', 'pending');
CREATE INDEX idx_verifications_user  ON user_verifications(user_id);
CREATE INDEX idx_verifications_queue ON user_verifications(submitted_at) WHERE status = 'pending';

CREATE TABLE verification_documents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    verification_id uuid NOT NULL REFERENCES user_verifications(id) ON DELETE CASCADE,
    storage_key     text NOT NULL UNIQUE,
    filename        text NOT NULL,
    size_bytes      int  NOT NULL CHECK (size_bytes > 0),
    uploaded_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
CREATE INDEX idx_verification_documents_verification ON verification_documents(verification_id);

CREATE TABLE risk_signals (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    signal_type text NOT NULL,
    weight      smallint NOT NULL DEFAULT 1,
    details     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_risk_signals_user ON risk_signals(user_id, created_at);

CREATE TABLE notifications (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type        text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}',
    channel     text NOT NULL DEFAULT 'telegram',
    dedupe_key  text UNIQUE,
    send_after  timestamptz NOT NULL DEFAULT now(),
    sent_at     timestamptz,
    read_at     timestamptz,
    attempts    smallint NOT NULL DEFAULT 0,
    last_error  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_notifications_pending ON notifications(send_after) WHERE sent_at IS NULL;
"""

DOWNGRADE = """
DROP TABLE notifications;
DROP TABLE risk_signals;
DROP TABLE verification_documents;
DROP TABLE user_verifications;
DROP TABLE units;
DROP TABLE properties;
DROP TABLE buildings;
DROP TABLE districts;
DROP TYPE ownership_status;
DROP TYPE unit_kind;
DROP TYPE property_kind;
DROP TYPE verification_status;
DROP TYPE verification_kind;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
