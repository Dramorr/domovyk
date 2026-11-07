"""M4: договори, ПІБ сторін для договору, підписи з порталу Дії

Таблиці як у db/schema.sql (розділ 6). Відхилення (схему оновлено, v0.9):
- contracts.conditions_hash — відбиток погоджених умов: змінилися умови → договір
  формується заново, старий стає void;
- contracts.void_reason, external_decided_at — для «договору поза Домовиком» (CTR-EXT);
- один «живий» договір на оренду (частковий унікальний індекс);
- contract_parties — ПІБ сторін для тексту договору (FR-CTR-1); у сертифікаті Дії
  ПІБ звіряється саме з ним;
- contract_signatures: reviewed_by / reviewed_at / review_reason — рішення модератора
  (поки немає криптоперевірки, ADR 0010); індекс за хешем РНОКПП (ліміт оренд, FR-LSE-8).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-07
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TYPE contract_source AS ENUM ('generated', 'uploaded_external');
CREATE TYPE contract_status AS ENUM ('draft', 'sent', 'partially_signed', 'signed', 'void');
CREATE TYPE signature_check AS ENUM ('not_checked', 'auto_ok', 'mismatch', 'manual_ok', 'invalid');
CREATE TYPE party_role      AS ENUM ('tenant', 'landlord');

CREATE TABLE contract_parties (
    lease_id        uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id),
    party_role      party_role NOT NULL,
    full_name       text NOT NULL CHECK (char_length(full_name) BETWEEN 5 AND 120),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lease_id, user_id)
);

CREATE TABLE contracts (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id            uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    kind                text NOT NULL DEFAULT 'lease',
    source              contract_source NOT NULL DEFAULT 'generated',
    template_code       text,
    version             smallint NOT NULL DEFAULT 1,
    pdf_storage_key     text NOT NULL,
    pdf_sha256          text NOT NULL,
    status              contract_status NOT NULL DEFAULT 'draft',
    data_snapshot       jsonb NOT NULL DEFAULT '{}',
    conditions_hash     text NOT NULL,
    void_reason         text,
    external_decided_at timestamptz,
    created_by          uuid NOT NULL REFERENCES users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    signed_at           timestamptz
);
CREATE INDEX idx_contracts_lease ON contracts(lease_id);
CREATE UNIQUE INDEX uq_contracts_live ON contracts(lease_id, kind)
    WHERE status IN ('draft', 'sent', 'partially_signed', 'signed');

CREATE TABLE contract_signatures (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id             uuid NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    user_id                 uuid NOT NULL REFERENCES users(id),
    party_role              party_role NOT NULL,
    signature_storage_key   text NOT NULL,
    signature_filename      text NOT NULL,
    signer_name             text,
    signer_tax_id_hash      text,
    signed_at_claimed       timestamptz,
    check_status            signature_check NOT NULL DEFAULT 'not_checked',
    check_details           jsonb NOT NULL DEFAULT '{}',
    uploaded_at             timestamptz NOT NULL DEFAULT now(),
    reviewed_by             uuid REFERENCES users(id),
    reviewed_at             timestamptz,
    review_reason           text,
    UNIQUE (contract_id, user_id)
);
CREATE INDEX idx_contract_signatures_tax ON contract_signatures(signer_tax_id_hash)
    WHERE signer_tax_id_hash IS NOT NULL;
CREATE INDEX idx_contract_signatures_review ON contract_signatures(uploaded_at)
    WHERE check_status IN ('not_checked', 'auto_ok', 'mismatch');
"""

DOWNGRADE = """
DROP TABLE contract_signatures;
DROP TABLE contracts;
DROP TABLE contract_parties;
DROP TYPE party_role;
DROP TYPE signature_check;
DROP TYPE contract_status;
DROP TYPE contract_source;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
