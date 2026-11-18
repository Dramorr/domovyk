"""Покази: слоти власника й запис із заявки (FR-APP-4, FR-APP-5, M6 частина 5)

showing_slots — час і місткість (груповий показ, якщо більше одного).
applications.slot_id — на який слот записано, і відповіді сторін після показу.
showing_done тепер теж відкрита заявка: після показу ще оформлюють оренду.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TYPE slot_status AS ENUM ('open', 'cancelled', 'done');

CREATE TABLE showing_slots (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id      uuid NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    host_user_id    uuid NOT NULL REFERENCES users(id),
    starts_at       timestamptz NOT NULL,
    ends_at         timestamptz NOT NULL,
    capacity        smallint NOT NULL DEFAULT 1 CHECK (capacity BETWEEN 1 AND 20),
    status          slot_status NOT NULL DEFAULT 'open',
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at)
);
CREATE INDEX idx_slots_listing_time ON showing_slots(listing_id, starts_at);

ALTER TABLE applications
    ADD COLUMN slot_id uuid REFERENCES showing_slots(id) ON DELETE SET NULL,
    ADD COLUMN showing_tenant_answer text
        CHECK (showing_tenant_answer IN ('done', 'no_show', 'moved')),
    ADD COLUMN showing_landlord_answer text
        CHECK (showing_landlord_answer IN ('done', 'no_show', 'moved'));
ALTER TABLE applications ADD CONSTRAINT applications_slot_status
    CHECK (slot_id IS NULL OR status NOT IN ('pending', 'approved'));
CREATE INDEX idx_applications_slot ON applications(slot_id) WHERE slot_id IS NOT NULL;

DROP INDEX uq_one_open_application;
CREATE UNIQUE INDEX uq_one_open_application
    ON applications(listing_id, tenant_user_id)
    WHERE status IN ('pending', 'approved', 'showing_scheduled', 'showing_done');
"""

DOWNGRADE = """
DROP INDEX uq_one_open_application;
CREATE UNIQUE INDEX uq_one_open_application
    ON applications(listing_id, tenant_user_id)
    WHERE status IN ('pending', 'approved', 'showing_scheduled');
DROP INDEX idx_applications_slot;
ALTER TABLE applications DROP CONSTRAINT applications_slot_status;
ALTER TABLE applications
    DROP COLUMN showing_landlord_answer,
    DROP COLUMN showing_tenant_answer,
    DROP COLUMN slot_id;
DROP TABLE showing_slots;
DROP TYPE slot_status;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
