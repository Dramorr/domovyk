"""M1: акаунти, згоди, профіль орендаря, бани, журнал дій, події

Таблиці як у db/schema.sql (розділи 1, 10, 11). Відхилення:
- user_consents — нова таблиця (FR-USR-1: факт і версія згоди), додана й у schema.sql;
- bans.case_id поки без FK: moderation_cases з'явиться в M7.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TYPE user_role         AS ENUM ('user', 'moderator', 'admin');
CREATE TYPE user_status       AS ENUM ('active', 'under_review', 'restricted', 'banned', 'deleted');
CREATE TYPE identity_provider AS ENUM ('telegram', 'phone', 'email', 'bankid', 'diia');
CREATE TYPE consent_document  AS ENUM ('terms', 'privacy');

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END $$;

CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name    text        NOT NULL,
    birth_date      date,
    role            user_role   NOT NULL DEFAULT 'user',
    status          user_status NOT NULL DEFAULT 'active',
    locale          text        NOT NULL DEFAULT 'uk',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    last_active_at  timestamptz,
    deleted_at      timestamptz
);
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE auth_identities (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider        identity_provider NOT NULL,
    provider_uid    text NOT NULL,
    is_contact      boolean NOT NULL DEFAULT false,
    verified_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_uid)
);
CREATE INDEX idx_auth_identities_user ON auth_identities(user_id);

CREATE TABLE tenant_profiles (
    user_id         uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    about           text,
    occupation      text,
    household_size  smallint CHECK (household_size BETWEEN 1 AND 20),
    has_children    boolean,
    has_pets        boolean,
    pets_details    text,
    smoker          boolean,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_tenant_profiles_updated BEFORE UPDATE ON tenant_profiles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE user_consents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document        consent_document NOT NULL,
    version         text NOT NULL,
    accepted_at     timestamptz NOT NULL
);
CREATE INDEX idx_user_consents_user ON user_consents(user_id, document);

CREATE TABLE bans (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid REFERENCES users(id),
    identity_hash   text,
    phone_hash      text,
    telegram_uid    text,
    reason          text NOT NULL,
    case_id         uuid,
    created_by      uuid REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz,
    lifted_at       timestamptz,
    CHECK (identity_hash IS NOT NULL OR phone_hash IS NOT NULL OR telegram_uid IS NOT NULL)
);
CREATE INDEX idx_bans_identity ON bans(identity_hash) WHERE identity_hash IS NOT NULL;
CREATE INDEX idx_bans_phone    ON bans(phone_hash)    WHERE phone_hash    IS NOT NULL;
CREATE INDEX idx_bans_telegram ON bans(telegram_uid)  WHERE telegram_uid  IS NOT NULL;

CREATE TABLE audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id    uuid REFERENCES users(id),
    action      text NOT NULL,
    entity_type text NOT NULL,
    entity_id   text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);

CREATE TABLE events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
    name        text NOT NULL,
    props       jsonb NOT NULL DEFAULT '{}',
    source      text NOT NULL DEFAULT 'telegram_bot',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_events_name_time ON events(name, created_at);
"""

DOWNGRADE = """
DROP TABLE events;
DROP TABLE audit_log;
DROP TABLE bans;
DROP TABLE user_consents;
DROP TABLE tenant_profiles;
DROP TABLE auth_identities;
DROP TABLE users;
DROP FUNCTION set_updated_at();
DROP TYPE consent_document;
DROP TYPE identity_provider;
DROP TYPE user_status;
DROP TYPE user_role;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
