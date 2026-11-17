"""Заявки орендарів на оголошення (FR-APP-1..3, 6, 7; M6 частина 4)

Слоти показів (showing_slots, slot_id) — у частині 5. Тут: заявка, історія статусів,
зовнішні ключі, які в M3 лишили на потім (leases.application_id, conversations.application_id).
Відкрита заявка (pending/approved/showing_scheduled) на оголошення від людини — одна.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TYPE application_status AS ENUM (
    'pending', 'approved', 'declined', 'withdrawn', 'expired',
    'showing_scheduled', 'showing_done', 'no_show',
    'lease_created'
);

CREATE TABLE applications (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id              uuid NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    tenant_user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status                  application_status NOT NULL DEFAULT 'pending',
    message                 text CHECK (message IS NULL OR char_length(message) <= 500),
    decline_reason          text CHECK (decline_reason IS NULL
                                        OR decline_reason IN ('rented', 'terms', 'other')),
    lease_id                uuid REFERENCES leases(id) ON DELETE SET NULL,  -- оренда із заявки
    contacts_revealed_at    timestamptz,
    decided_at              timestamptz,
    expires_at              timestamptz NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_one_open_application
    ON applications(listing_id, tenant_user_id)
    WHERE status IN ('pending', 'approved', 'showing_scheduled');
CREATE INDEX idx_applications_listing ON applications(listing_id, status);
CREATE INDEX idx_applications_tenant ON applications(tenant_user_id, created_at DESC);
CREATE INDEX idx_applications_expiry ON applications(expires_at) WHERE status = 'pending';
CREATE TRIGGER trg_applications_updated BEFORE UPDATE ON applications
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE application_status_history (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_id  uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    from_status     application_status,
    to_status       application_status NOT NULL,
    changed_by      uuid REFERENCES users(id) ON DELETE SET NULL,
    changed_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_app_history_app ON application_status_history(application_id);

ALTER TABLE leases ADD CONSTRAINT leases_application_id_fkey
    FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE SET NULL;
ALTER TABLE conversations ADD CONSTRAINT conversations_application_id_fkey
    FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX uq_conversation_application ON conversations(application_id)
    WHERE application_id IS NOT NULL;
"""

DOWNGRADE = """
DROP INDEX uq_conversation_application;
ALTER TABLE conversations DROP CONSTRAINT conversations_application_id_fkey;
ALTER TABLE leases DROP CONSTRAINT leases_application_id_fkey;
DROP TABLE application_status_history;
DROP TABLE applications;
DROP TYPE application_status;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
