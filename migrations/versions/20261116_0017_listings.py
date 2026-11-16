"""Оголошення власника (FR-LST-1..5, M6 частина 1, ADR 0018)

Оголошення не дублює анкету об'єкта: опис, тварини, діти, мешканці, реєстрація й
зручності беруться з properties.details. Тут — лише умови угоди й стан публікації.
Живе (draft/active/paused) оголошення на юніт одне; здані, зняті й заблоковані
лишаються історією (на них посилатимуться заявки й відгуки).

risk_words — «ріелторські» слова для FR-LST-5: дані в БД, не код. Збіг не блокує
публікацію, а пише risk_signal і кличе модераторів.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

from domovyk.core.db import split_sql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TYPE listing_status AS ENUM ('draft', 'active', 'paused', 'rented', 'archived', 'blocked');

CREATE TABLE listings (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id                 uuid NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    created_by              uuid NOT NULL REFERENCES users(id),
    price_amount            integer NOT NULL CHECK (price_amount BETWEEN 100 AND 1000000),
    currency                currency_code NOT NULL DEFAULT 'UAH',
    utilities_included      boolean NOT NULL DEFAULT false,
    deposit_amount          integer CHECK (deposit_amount IS NULL
                                           OR deposit_amount BETWEEN 0 AND 3000000),
    min_term_months         smallint CHECK (min_term_months BETWEEN 1 AND 35),
    available_from          date,
    status                  listing_status NOT NULL DEFAULT 'draft',
    owner_declaration_at    timestamptz,
    published_at            timestamptz,
    closed_at               timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CHECK (status = 'draft' OR owner_declaration_at IS NOT NULL),
    CHECK (status = 'draft' OR published_at IS NOT NULL)
);
CREATE UNIQUE INDEX uq_one_live_listing_per_unit
    ON listings(unit_id) WHERE status IN ('draft', 'active', 'paused');
CREATE INDEX idx_listings_search ON listings(status, price_amount);
CREATE INDEX idx_listings_author ON listings(created_by, published_at);
CREATE TRIGGER trg_listings_updated BEFORE UPDATE ON listings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE risk_words (
    id          serial PRIMARY KEY,
    word        text NOT NULL UNIQUE
                CHECK (word = lower(word) AND char_length(word) BETWEEN 3 AND 40),
    created_at  timestamptz NOT NULL DEFAULT now()
);
-- Основи слів: збіг — слово в тексті, що починається з основи («комісі» → «комісія»).
-- Список редагують модератори. «Послуги» свідомо немає: «комунальні послуги» в кожному описі.
INSERT INTO risk_words (word) VALUES
    ('комісі'), ('агенці'), ('ріелтор'), ('рієлтор'), ('ріелтер'),
    ('рієлтер'), ('маклер'), ('посередни'), ('агент');
"""

DOWNGRADE = """
DROP TABLE risk_words;
DROP TABLE listings;
DROP TYPE listing_status;
"""


def upgrade() -> None:
    for statement in split_sql(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in split_sql(DOWNGRADE):
        op.execute(statement)
