"""M5 (частина 1): нарахування й оплати — лише облік (FR-PAY-1..6)

Таблиці як у db/schema.sql (розділ 7). Відхилення (схему оновлено, v0.13):
- payments.receipt_filename / receipt_mime — щоб показати й переслати квитанцію;
- payments.note — коментар орендаря до оплати;
- нагадування не мають окремих позначок: дублі відсікає outbox за dedupe_key.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TYPE charge_status  AS ENUM (
    'upcoming', 'due', 'overdue', 'reported', 'paid', 'disputed', 'waived'
);
CREATE TYPE payment_status AS ENUM (
    'auto_accepted', 'pending_landlord', 'confirmed', 'disputed', 'rejected'
);

CREATE TABLE rent_charges (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    period_month    date NOT NULL CHECK (extract(day FROM period_month) = 1),
    kind            text NOT NULL DEFAULT 'rent'
                    CHECK (kind IN ('rent', 'deposit', 'utilities', 'other')),
    amount          numeric(12,2) NOT NULL CHECK (amount > 0),
    currency        currency_code NOT NULL DEFAULT 'UAH',
    due_date        date NOT NULL,
    status          charge_status NOT NULL DEFAULT 'upcoming',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (lease_id, period_month, kind)
);
CREATE INDEX idx_rent_charges_due ON rent_charges(due_date)
    WHERE status IN ('upcoming', 'due', 'overdue');
CREATE INDEX idx_rent_charges_lease ON rent_charges(lease_id, due_date DESC);

CREATE TABLE payments (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    charge_id           uuid NOT NULL REFERENCES rent_charges(id) ON DELETE CASCADE,
    payer_user_id       uuid NOT NULL REFERENCES users(id),
    method              pay_method NOT NULL,
    amount              numeric(12,2) NOT NULL CHECK (amount > 0),
    paid_at             timestamptz,
    receipt_storage_key text,
    receipt_filename    text,
    receipt_mime        text,
    receipt_sha256      text UNIQUE,
    bank_reference      text,
    parsed              jsonb NOT NULL DEFAULT '{}',
    mismatches          text[] NOT NULL DEFAULT '{}',
    note                text CHECK (note IS NULL OR char_length(note) <= 300),
    status              payment_status NOT NULL,
    dispute_deadline    timestamptz,
    decided_by          uuid REFERENCES users(id),
    decided_at          timestamptz,
    dispute_reason      text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (method = 'cash' OR receipt_storage_key IS NOT NULL)
);
CREATE UNIQUE INDEX uq_payment_bank_ref ON payments(bank_reference)
    WHERE bank_reference IS NOT NULL;
CREATE INDEX idx_payments_charge ON payments(charge_id);
CREATE INDEX idx_payments_dispute_window ON payments(dispute_deadline)
    WHERE status = 'auto_accepted';
CREATE INDEX idx_payments_pending ON payments(created_at) WHERE status = 'pending_landlord';
"""

DOWNGRADE = """
DROP TABLE payments;
DROP TABLE rent_charges;
DROP TYPE payment_status;
DROP TYPE charge_status;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
