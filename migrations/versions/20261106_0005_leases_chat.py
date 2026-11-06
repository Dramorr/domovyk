"""M3: оренди, учасники, запрошення, чат оренди, налаштування сповіщень

Таблиці як у db/schema.sql (розділи 4, 9, 12). Відхилення (схему оновлено, v0.6):
- leases.application_id поки без FK — заявки з'являться в M6;
- leases.pay_details — лише замасковане для показу ({"masked", "recipient"}),
  повні реквізити — leases.pay_details_enc (Fernet, FR-PAY-8);
- leases.terms_accepted_at — коли відповідальний орендар погодив умови (LSE-TERMS);
- conversations.application_id без FK (M6);
- conversation_reads замість message_deliveries: чат живе в Mini App, а не в
  Telegram, тож достатньо «до якого повідомлення прочитано».

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TYPE currency_code AS ENUM ('UAH', 'USD', 'EUR');
CREATE TYPE lease_origin  AS ENUM ('application', 'invite');
CREATE TYPE lease_status  AS ENUM (
    'draft', 'awaiting_tenant', 'awaiting_signatures', 'active', 'ending', 'ended', 'cancelled'
);
CREATE TYPE member_role   AS ENUM ('responsible', 'co_tenant');
CREATE TYPE pay_method    AS ENUM ('card', 'iban', 'jar', 'cash');

CREATE TABLE leases (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id             uuid NOT NULL REFERENCES units(id),
    landlord_user_id    uuid NOT NULL REFERENCES users(id),
    origin              lease_origin NOT NULL,
    application_id      uuid,
    status              lease_status NOT NULL DEFAULT 'draft',
    start_date          date NOT NULL,
    end_date            date NOT NULL,
    rent_amount         integer NOT NULL CHECK (rent_amount > 0),
    currency            currency_code NOT NULL DEFAULT 'UAH',
    rent_due_day        smallint NOT NULL CHECK (rent_due_day BETWEEN 1 AND 28),
    deposit_amount      integer CHECK (deposit_amount >= 0),
    pay_method          pay_method NOT NULL,
    pay_details         jsonb NOT NULL DEFAULT '{}',
    pay_details_enc     text,
    terms               jsonb NOT NULL DEFAULT '{}',
    settings            jsonb NOT NULL DEFAULT '{}',
    terms_accepted_at   timestamptz,
    activated_at        timestamptz,
    ended_at            timestamptz,
    cancelled_at        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (end_date > start_date),
    CHECK (end_date <= start_date + interval '35 months'),
    CHECK (origin <> 'application' OR application_id IS NOT NULL)
);
CREATE INDEX idx_leases_unit ON leases(unit_id);
CREATE INDEX idx_leases_landlord ON leases(landlord_user_id);
CREATE TRIGGER trg_leases_updated BEFORE UPDATE ON leases
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE lease_members (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id),
    member_role     member_role NOT NULL,
    utility_share   numeric(5,2),
    joined_at       timestamptz NOT NULL DEFAULT now(),
    left_at         timestamptz,
    UNIQUE (lease_id, user_id)
);
-- Рівно один відповідальний орендар на оренду (поки він не вийшов).
CREATE UNIQUE INDEX uq_one_responsible
    ON lease_members(lease_id) WHERE member_role = 'responsible' AND left_at IS NULL;
CREATE INDEX idx_lease_members_user ON lease_members(user_id);

CREATE TABLE lease_invites (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    token_hash      text NOT NULL UNIQUE,
    member_role     member_role NOT NULL,
    created_by      uuid NOT NULL REFERENCES users(id),
    expires_at      timestamptz NOT NULL,
    used_by         uuid REFERENCES users(id),
    used_at         timestamptz,
    revoked_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_lease_invites_lease ON lease_invites(lease_id);

CREATE TABLE conversations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid REFERENCES leases(id) ON DELETE CASCADE,
    application_id  uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((lease_id IS NOT NULL) <> (application_id IS NOT NULL))
);
CREATE UNIQUE INDEX uq_conversation_lease ON conversations(lease_id) WHERE lease_id IS NOT NULL;
CREATE UNIQUE INDEX uq_conversation_app ON conversations(application_id)
    WHERE application_id IS NOT NULL;

CREATE TABLE messages (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id     uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id           uuid REFERENCES users(id),
    body                text,
    attachment_key      text,
    attachment_type     text,
    system_code         text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    edited_at           timestamptz,
    deleted_at          timestamptz,
    CHECK (body IS NOT NULL OR attachment_key IS NOT NULL OR system_code IS NOT NULL)
);
CREATE INDEX idx_messages_conv ON messages(conversation_id, id);

CREATE TABLE conversation_reads (
    conversation_id      uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_read_message_id bigint NOT NULL DEFAULT 0,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, user_id)
);

CREATE TABLE notification_preferences (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lease_id    uuid REFERENCES leases(id) ON DELETE CASCADE,
    type        text NOT NULL,
    channel     text NOT NULL DEFAULT 'telegram',
    enabled     boolean NOT NULL DEFAULT true,
    params      jsonb NOT NULL DEFAULT '{}',
    UNIQUE NULLS NOT DISTINCT (user_id, lease_id, type, channel)
);
"""

DOWNGRADE = """
DROP TABLE notification_preferences;
DROP TABLE conversation_reads;
DROP TABLE messages;
DROP TABLE conversations;
DROP TABLE lease_invites;
DROP TABLE lease_members;
DROP TABLE leases;
DROP TYPE pay_method;
DROP TYPE member_role;
DROP TYPE lease_status;
DROP TYPE lease_origin;
DROP TYPE currency_code;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
