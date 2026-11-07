-- =====================================================================
--  Домовик — цільова схема бази даних, v0.9
--  PostgreSQL 16 + PostGIS
--
--  Це ЦІЛЬОВА модель. Реальні зміни в базі робляться тільки
--  міграціями Alembic, крок за кроком, по мірі реалізації етапів.
--  Якщо міграція відходить від цього файлу — онови файл у тому ж коміті.
--
--  Принципи:
--   • Користувач ≠ Telegram ID. Способи входу — в auth_identities.
--   • «Орендар» і «орендодавець» — не тип акаунта, а роль у конкретній
--     оренді. Тип акаунта (users.role) — лише user / moderator / admin.
--   • properties (фізичний об'єкт: квартира/будинок) → units (що саме
--     здається: весь об'єкт або кімната) → listings (оголошення) і
--     leases (оренда = «картка об'єкта» для обох сторін).
--   • Домовик НЕ проводить гроші. payments — лише облік фактів оплати
--     (квитанції, підтвердження готівки).
--   • Бан прив'язується до хешів ідентифікаторів, а не лише до номера.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------
--  ENUM-и
-- ---------------------------------------------------------------------
CREATE TYPE user_role           AS ENUM ('user', 'moderator', 'admin');
CREATE TYPE user_status         AS ENUM ('active', 'under_review', 'restricted', 'banned', 'deleted');
CREATE TYPE identity_provider   AS ENUM ('telegram', 'phone', 'email', 'bankid', 'diia');
CREATE TYPE verification_kind   AS ENUM ('phone', 'identity', 'ownership');
CREATE TYPE verification_status AS ENUM ('draft', 'pending', 'verified', 'rejected', 'expired');  -- draft: власник ще додає документи
CREATE TYPE currency_code       AS ENUM ('UAH', 'USD', 'EUR');
CREATE TYPE consent_document    AS ENUM ('terms', 'privacy');

CREATE TYPE property_kind       AS ENUM ('apartment', 'house');
CREATE TYPE unit_kind           AS ENUM ('entire', 'room');
CREATE TYPE ownership_status    AS ENUM ('unverified', 'pending', 'verified', 'rejected');

CREATE TYPE listing_status      AS ENUM ('draft', 'active', 'paused', 'rented', 'archived', 'blocked');
CREATE TYPE slot_status         AS ENUM ('open', 'cancelled', 'done');
CREATE TYPE application_status  AS ENUM (
    'pending', 'approved', 'declined', 'withdrawn', 'expired',
    'showing_scheduled', 'showing_done', 'no_show',
    'lease_created'          -- з цієї заявки створено оренду
);

CREATE TYPE lease_origin        AS ENUM ('application', 'invite');
CREATE TYPE lease_status        AS ENUM (
    'draft',                 -- власник заповнює умови
    'awaiting_tenant',       -- чекаємо, поки відповідальний орендар приєднається
    'awaiting_signatures',   -- договір сформовано, чекаємо підписи
    'active',                -- обидва підписали (або «договір поза Домовиком» підтверджено)
    'ending',                -- хтось повідомив про завершення
    'ended',
    'cancelled'
);
CREATE TYPE member_role         AS ENUM ('responsible', 'co_tenant');
CREATE TYPE contract_source     AS ENUM ('generated', 'uploaded_external');
CREATE TYPE contract_status     AS ENUM ('draft', 'sent', 'partially_signed', 'signed', 'void');
CREATE TYPE signature_check     AS ENUM ('not_checked', 'auto_ok', 'mismatch', 'manual_ok', 'invalid');

CREATE TYPE pay_method          AS ENUM ('card', 'iban', 'jar', 'cash');
CREATE TYPE charge_status       AS ENUM ('upcoming', 'due', 'overdue', 'reported', 'paid', 'disputed', 'waived');
CREATE TYPE payment_status      AS ENUM (
    'auto_accepted',         -- квитанція збіглась, йде вікно оскарження
    'pending_landlord',      -- потрібне ручне підтвердження власника (готівка або розбіжність)
    'confirmed',
    'disputed',
    'rejected'
);

CREATE TYPE meter_kind          AS ENUM ('electricity', 'water_cold', 'water_hot', 'gas', 'heat');
CREATE TYPE request_status      AS ENUM ('open', 'in_progress', 'resolved', 'cancelled');
CREATE TYPE visit_status        AS ENUM ('proposed', 'accepted', 'declined', 'cancelled', 'done');

CREATE TYPE party_role          AS ENUM ('tenant', 'landlord');
CREATE TYPE complaint_reason    AS ENUM (
    'intermediary', 'fake_listing', 'fraud', 'misleading_info', 'no_show', 'rude', 'other'
);
CREATE TYPE complaint_status    AS ENUM ('new', 'dismissed', 'confirmed');
CREATE TYPE case_trigger        AS ENUM ('complaints_threshold', 'risk_signal', 'manual');
CREATE TYPE case_status         AS ENUM ('open', 'in_progress', 'resolved');

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END $$;

-- =====================================================================
--  1. КОРИСТУВАЧІ ТА ВХІД
-- =====================================================================
CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name    text        NOT NULL,
    birth_date      date,
    role            user_role   NOT NULL DEFAULT 'user',   -- moderator призначає admin
    status          user_status NOT NULL DEFAULT 'active',
    locale          text        NOT NULL DEFAULT 'uk',
    app_mode        text CHECK (app_mode IN ('tenant', 'landlord')),  -- режим застосунку (ADR 0006); NULL — ще не обрав
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
    provider_uid    text NOT NULL,          -- tg user id / номер E.164 / email
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

-- Згоди з правилами й політикою конфіденційності (FR-USR-1): факт і версія документа.
-- Кожна нова версія, з якою людина погодилась, — новий рядок (історія лишається).
CREATE TABLE user_consents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document        consent_document NOT NULL,
    version         text NOT NULL,
    accepted_at     timestamptz NOT NULL
);
CREATE INDEX idx_user_consents_user ON user_consents(user_id, document);

-- =====================================================================
--  2. АДРЕСИ, ОБ'ЄКТИ, ВЛАСНІСТЬ
-- =====================================================================
-- Довідник міст (ADR 0008): починаємо зі Львова, далі — інші міста.
-- Усе міське (будинки, райони, постачальники, пошук) посилається на cities.code.
CREATE TABLE cities (
    code        text PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9_]{1,30}$'),  -- 'lviv'
    name        text NOT NULL UNIQUE,                                      -- 'Львів'
    region      text NOT NULL,
    status      text NOT NULL DEFAULT 'soon' CHECK (status IN ('active', 'soon')),
    timezone    text NOT NULL DEFAULT 'Europe/Kyiv',
    sort_order  smallint NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE districts (
    id          serial PRIMARY KEY,
    city_code   text NOT NULL REFERENCES cities(code),
    name        text NOT NULL,
    parent_id   int REFERENCES districts(id),
    UNIQUE (city_code, name)
);

CREATE TABLE buildings (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    city_code           text NOT NULL REFERENCES cities(code),
    street              text NOT NULL,
    house_number        text NOT NULL,
    normalized_key      text NOT NULL UNIQUE,   -- 'lviv|вул болгарська|5'
    district_id         int REFERENCES districts(id),
    location            geography(Point, 4326),
    geocoder_place_id   text,
    floors_total        smallint,
    has_backup_power    boolean,                -- генератор будинку
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_buildings_location ON buildings USING gist(location);
CREATE INDEX idx_buildings_district ON buildings(district_id);
CREATE INDEX idx_buildings_city ON buildings(city_code);

-- Фізичний об'єкт: квартира або будинок.
CREATE TABLE properties (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id           uuid NOT NULL REFERENCES users(id),     -- орендодавець, не може бути порожнім
    kind                    property_kind NOT NULL DEFAULT 'apartment',
    building_id             uuid NOT NULL REFERENCES buildings(id),
    apartment_number        text,
    apartment_number_norm   text,               -- NULL для будинку
    entrance                text,
    floor                   smallint,
    rooms                   smallint NOT NULL CHECK (rooms BETWEEN 0 AND 30),  -- 0 = студія
    area_total              numeric(7,1) CHECK (area_total > 0),
    ownership_status        ownership_status NOT NULL DEFAULT 'unverified',
    ownership_verified_at   timestamptz,
    settings                jsonb NOT NULL DEFAULT '{}',   -- внутрішні налаштування об'єкта
    details                 jsonb NOT NULL DEFAULT '{}',   -- анкета об'єкта (FR-PRP-8, ADR 0003): PropertyDetails + "v"
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CHECK (kind = 'house' OR apartment_number_norm IS NOT NULL)
);
CREATE UNIQUE INDEX uq_property_address
    ON properties(building_id, COALESCE(apartment_number_norm, ''));
CREATE INDEX idx_properties_owner ON properties(owner_user_id);
CREATE TRIGGER trg_properties_updated BEFORE UPDATE ON properties
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Що саме здається: весь об'єкт (entire) або окрема кімната (room).
-- Сервіс не дає одночасно здавати entire і room одного об'єкта.
CREATE TABLE units (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id     uuid NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    kind            unit_kind NOT NULL DEFAULT 'entire',
    name            text NOT NULL,              -- 'Вся квартира', 'Кімната з балконом'
    area            numeric(7,1),
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_one_entire_unit ON units(property_id) WHERE kind = 'entire';
CREATE INDEX idx_units_property ON units(property_id);

CREATE TABLE property_delegates (
    property_id     uuid NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    can_manage_slots        boolean NOT NULL DEFAULT true,
    can_answer_applications boolean NOT NULL DEFAULT false,
    added_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (property_id, user_id)
);

-- Верифікації. Для ownership: власник завантажує інформаційну довідку
-- з ДРРП (PDF з Дії) і, якщо треба, довіреність чи згоду співвласника
-- (verification_documents), модератор перевіряє. Дубль перевірки — збіг ПІБ
-- з сертифіката Дія.Підпису на договорі.
CREATE TABLE user_verifications (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            verification_kind   NOT NULL,
    status          verification_status NOT NULL DEFAULT 'pending',
    provider        identity_provider,
    identity_hash   text,       -- HMAC-SHA256(РНОКПП, секрет). Сам номер не зберігаємо.
    property_id     uuid REFERENCES properties(id) ON DELETE CASCADE,
    owner_name_in_doc text,     -- ПІБ власника(ів) з довідки — для звірки з підписом
    is_dev_bypass   boolean NOT NULL DEFAULT false,  -- створено прапорцем DEV_SKIP_OWNERSHIP_CHECK
    reviewed_by     uuid REFERENCES users(id),
    reject_reason   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    submitted_at    timestamptz,   -- draft → pending
    decided_at      timestamptz,
    CHECK (kind <> 'ownership' OR property_id IS NOT NULL)
);
CREATE UNIQUE INDEX uq_verified_identity
    ON user_verifications(identity_hash)
    WHERE kind = 'identity' AND status = 'verified';
-- Одна відкрита перевірка власності на об'єкт.
CREATE UNIQUE INDEX uq_open_ownership_verification
    ON user_verifications(property_id)
    WHERE kind = 'ownership' AND status IN ('draft', 'pending');
CREATE INDEX idx_verifications_user  ON user_verifications(user_id);
CREATE INDEX idx_verifications_queue ON user_verifications(submitted_at) WHERE status = 'pending';

-- Файли до перевірки. Лежать у сховищі зашифрованими (Fernet); видаляються
-- через OWNERSHIP_DOC_RETENTION_DAYS після рішення (deleted_at, рядок лишається).
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

-- =====================================================================
--  3. ОГОЛОШЕННЯ
-- =====================================================================
CREATE TABLE listings (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id                 uuid NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    created_by              uuid NOT NULL REFERENCES users(id),
    title                   text NOT NULL,
    description             text,
    price_amount            integer NOT NULL CHECK (price_amount > 0),
    currency                currency_code NOT NULL DEFAULT 'UAH',
    utilities_included      boolean NOT NULL DEFAULT false,
    utilities_estimate      integer,
    deposit_amount          integer,
    min_term_months         smallint CHECK (min_term_months BETWEEN 1 AND 35),
    available_from          date,
    max_occupants           smallint,
    pets_allowed            boolean,
    children_allowed        boolean,
    residence_registration_allowed boolean,     -- згода на реєстрацію місця проживання
    status                  listing_status NOT NULL DEFAULT 'draft',
    owner_declaration_at    timestamptz NOT NULL,  -- «я власник / уповноважений, без комісії»
    published_at            timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_one_live_listing_per_unit
    ON listings(unit_id) WHERE status IN ('draft', 'active', 'paused');
CREATE INDEX idx_listings_search ON listings(status, price_amount);
CREATE TRIGGER trg_listings_updated BEFORE UPDATE ON listings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE amenities (
    id      serial PRIMARY KEY,
    code    text NOT NULL UNIQUE,
    name_uk text NOT NULL
);

CREATE TABLE listing_amenities (
    listing_id  uuid NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    amenity_id  int  NOT NULL REFERENCES amenities(id),
    PRIMARY KEY (listing_id, amenity_id)
);
CREATE INDEX idx_listing_amenities_amenity ON listing_amenities(amenity_id);

CREATE TABLE listing_photos (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id          uuid NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    storage_key         text NOT NULL,
    telegram_file_id    text,
    position            smallint NOT NULL DEFAULT 0,
    phash               bigint,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_listing_photos_listing ON listing_photos(listing_id, position);
CREATE INDEX idx_listing_photos_phash   ON listing_photos(phash);

-- =====================================================================
--  4. ПОШУК, ЗАЯВКИ, ПОКАЗИ
-- =====================================================================
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

CREATE TABLE applications (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id              uuid NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    tenant_user_id          uuid NOT NULL REFERENCES users(id),
    status                  application_status NOT NULL DEFAULT 'pending',
    message                 text,
    slot_id                 uuid REFERENCES showing_slots(id),
    contacts_revealed_at    timestamptz,
    decided_at              timestamptz,
    expires_at              timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_active_application
    ON applications(listing_id, tenant_user_id)
    WHERE status IN ('pending', 'approved', 'showing_scheduled');
CREATE INDEX idx_applications_tenant  ON applications(tenant_user_id, status);
CREATE INDEX idx_applications_listing ON applications(listing_id, status);
CREATE TRIGGER trg_applications_updated BEFORE UPDATE ON applications
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE application_status_history (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_id  uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    from_status     application_status,
    to_status       application_status NOT NULL,
    changed_by      uuid REFERENCES users(id),
    changed_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_app_history_app ON application_status_history(application_id);

CREATE TABLE favorites (
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    listing_id  uuid NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, listing_id)
);

CREATE TABLE search_filters (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                    text,
    city_code               text NOT NULL REFERENCES cities(code),  -- пошук завжди в одному місті
    property_kinds          property_kind[] NOT NULL DEFAULT '{}',
    unit_kinds              unit_kind[]     NOT NULL DEFAULT '{}',
    district_ids            int[] NOT NULL DEFAULT '{}',
    rooms_min               smallint,
    rooms_max               smallint,
    price_min               integer,
    price_max               integer,
    currency                currency_code NOT NULL DEFAULT 'UAH',
    area_min                numeric(7,1),
    floor_not_first         boolean NOT NULL DEFAULT false,
    floor_not_last          boolean NOT NULL DEFAULT false,
    need_pets_allowed       boolean NOT NULL DEFAULT false,
    need_children_allowed   boolean NOT NULL DEFAULT false,
    need_registration       boolean NOT NULL DEFAULT false,
    required_amenity_ids    int[] NOT NULL DEFAULT '{}',
    notify                  boolean NOT NULL DEFAULT true,
    is_active               boolean NOT NULL DEFAULT true,
    last_notified_at        timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_search_filters_active ON search_filters(is_active, notify);
CREATE TRIGGER trg_search_filters_updated BEFORE UPDATE ON search_filters
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =====================================================================
--  5. ОРЕНДА («картка об'єкта»)
-- =====================================================================
CREATE TABLE leases (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id             uuid NOT NULL REFERENCES units(id),
    landlord_user_id    uuid NOT NULL REFERENCES users(id),
    origin              lease_origin NOT NULL,
    application_id      uuid REFERENCES applications(id),
    status              lease_status NOT NULL DEFAULT 'draft',
    start_date          date NOT NULL,
    end_date            date NOT NULL,      -- строк обов'язковий; ≤ 35 міс. (без нотаріуса)
    rent_amount         integer NOT NULL CHECK (rent_amount > 0),
    currency            currency_code NOT NULL DEFAULT 'UAH',
    rent_due_day        smallint NOT NULL CHECK (rent_due_day BETWEEN 1 AND 28),
    deposit_amount      integer,
    pay_method          pay_method NOT NULL,
    pay_details         jsonb NOT NULL DEFAULT '{}',  -- лише для показу: {masked, recipient (скорочено)}
    pay_details_enc     text,                         -- повні реквізити, Fernet (FR-PAY-8)
    terms               jsonb NOT NULL DEFAULT '{}',  -- пункти договору: доступ, ремонти, тварини, КП, реєстрація…
    settings            jsonb NOT NULL DEFAULT '{}',  -- вікно оскарження оплати, нагадування тощо
    terms_accepted_at   timestamptz,                  -- відповідальний орендар погодив умови
    activated_at        timestamptz,
    ended_at            timestamptz,
    cancelled_at        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (end_date > start_date),
    CHECK (end_date <= start_date + interval '35 months'),
    CHECK (origin <> 'application' OR application_id IS NOT NULL)
);
-- Одна жива оренда на unit.
CREATE UNIQUE INDEX uq_one_live_lease_per_unit
    ON leases(unit_id) WHERE status IN ('awaiting_tenant', 'awaiting_signatures', 'active', 'ending');
CREATE INDEX idx_leases_landlord ON leases(landlord_user_id, status);
CREATE TRIGGER trg_leases_updated BEFORE UPDATE ON leases
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Хто живе. Обов'язковий лише один responsible; co_tenant — опційно,
-- без жодних обов'язкових дій (бачить картку, чат, нагадування — якщо увімкне).
CREATE TABLE lease_members (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id),
    member_role     member_role NOT NULL,
    utility_share   numeric(5,2),          -- % участі в КП; NULL = не ділимо
    joined_at       timestamptz NOT NULL DEFAULT now(),
    left_at         timestamptz,
    UNIQUE (lease_id, user_id)
);
-- Рівно один відповідальний орендар на оренду.
CREATE UNIQUE INDEX uq_one_responsible
    ON lease_members(lease_id) WHERE member_role = 'responsible' AND left_at IS NULL;
CREATE UNIQUE INDEX uq_one_responsible_per_lease
    ON lease_members(lease_id) WHERE member_role = 'responsible' AND left_at IS NULL;
CREATE INDEX idx_lease_members_user ON lease_members(user_id) WHERE left_at IS NULL;

-- Запрошення за посиланням (існуючі пари + співмешканці).
CREATE TABLE lease_invites (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    token_hash      text NOT NULL UNIQUE,   -- в посиланні сам токен, у базі лише хеш
    member_role     member_role NOT NULL,
    created_by      uuid NOT NULL REFERENCES users(id),
    expires_at      timestamptz NOT NULL,
    used_by         uuid REFERENCES users(id),
    used_at         timestamptz,
    revoked_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- =====================================================================
--  6. ДОГОВОРИ (підписання через портал Дії)
-- =====================================================================
-- ПІБ сторін для тексту договору (FR-CTR-1). ПІБ із сертифіката Дії звіряється з ним.
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
    kind                text NOT NULL DEFAULT 'lease',   -- 'lease', 'acceptance_act', 'termination', 'addendum'
    source              contract_source NOT NULL DEFAULT 'generated',
    template_code       text,                  -- 'lease_v1'
    version             smallint NOT NULL DEFAULT 1,
    pdf_storage_key     text NOT NULL,         -- PDF у сховищі зашифрований
    pdf_sha256          text NOT NULL,         -- фіксує, що підписували саме цей файл
    status              contract_status NOT NULL DEFAULT 'draft',
    data_snapshot       jsonb NOT NULL DEFAULT '{}',  -- дані, з яких згенеровано PDF
    conditions_hash     text NOT NULL,         -- відбиток погоджених умов; змінились → новий договір
    void_reason         text,                  -- 'superseded', 'conditions_changed', 'external_rejected', 'lease_cancelled'
    external_decided_at timestamptz,           -- CTR-EXT: орендар підтвердив / відхилив
    created_by          uuid NOT NULL REFERENCES users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    signed_at           timestamptz
);
CREATE INDEX idx_contracts_lease ON contracts(lease_id);
-- Один «живий» або підписаний договір кожного виду на оренду.
CREATE UNIQUE INDEX uq_contracts_live ON contracts(lease_id, kind)
    WHERE status IN ('draft', 'sent', 'partially_signed', 'signed');

CREATE TABLE contract_signatures (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id             uuid NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    user_id                 uuid NOT NULL REFERENCES users(id),
    party_role              party_role NOT NULL,
    signature_storage_key   text NOT NULL,      -- .p7s / .asice з порталу Дії (зашифровано)
    signature_filename      text NOT NULL,
    signer_name             text,               -- з сертифіката
    signer_tax_id_hash      text,               -- HMAC(РНОКПП з сертифіката)
    signed_at_claimed       timestamptz,        -- час підпису з контейнера
    check_status            signature_check NOT NULL DEFAULT 'not_checked',
    check_details           jsonb NOT NULL DEFAULT '{}',  -- без персональних даних
    uploaded_at             timestamptz NOT NULL DEFAULT now(),
    reviewed_by             uuid REFERENCES users(id),    -- модератор (ADR 0010)
    reviewed_at             timestamptz,
    review_reason           text,
    UNIQUE (contract_id, user_id)
);
CREATE INDEX idx_contract_signatures_tax ON contract_signatures(signer_tax_id_hash)
    WHERE signer_tax_id_hash IS NOT NULL;
CREATE INDEX idx_contract_signatures_review ON contract_signatures(uploaded_at)
    WHERE check_status IN ('not_checked', 'auto_ok', 'mismatch');

-- =====================================================================
--  7. ОПЛАТИ (тільки облік — гроші йдуть напряму власнику)
-- =====================================================================
-- Нарахування за період (генерує воркер за rent_due_day).
CREATE TABLE rent_charges (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    period_month    date NOT NULL,         -- перше число місяця
    kind            text NOT NULL DEFAULT 'rent',   -- 'rent', 'deposit', 'utilities', 'other'
    amount          numeric(12,2) NOT NULL CHECK (amount > 0),
    currency        currency_code NOT NULL DEFAULT 'UAH',
    due_date        date NOT NULL,
    status          charge_status NOT NULL DEFAULT 'upcoming',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (lease_id, period_month, kind)
);
CREATE INDEX idx_rent_charges_due ON rent_charges(due_date) WHERE status IN ('upcoming', 'due', 'overdue');
CREATE TRIGGER trg_rent_charges_updated BEFORE UPDATE ON rent_charges
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Факт оплати: квитанція (картка/IBAN/банка) або готівка.
CREATE TABLE payments (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    charge_id           uuid NOT NULL REFERENCES rent_charges(id) ON DELETE CASCADE,
    payer_user_id       uuid NOT NULL REFERENCES users(id),
    method              pay_method NOT NULL,
    amount              numeric(12,2) NOT NULL CHECK (amount > 0),
    paid_at             timestamptz,
    receipt_storage_key text,
    receipt_sha256      text UNIQUE,       -- той самий файл двічі не пройде
    bank_reference      text,              -- номер операції з квитанції
    parsed              jsonb NOT NULL DEFAULT '{}',   -- що витягнув парсер
    mismatches          text[] NOT NULL DEFAULT '{}',  -- 'amount', 'recipient', 'date', 'unparsed'
    status              payment_status NOT NULL,
    dispute_deadline    timestamptz,       -- для auto_accepted
    decided_by          uuid REFERENCES users(id),
    decided_at          timestamptz,
    dispute_reason      text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (method = 'cash' OR receipt_storage_key IS NOT NULL)
);
CREATE UNIQUE INDEX uq_payment_bank_ref ON payments(bank_reference) WHERE bank_reference IS NOT NULL;
CREATE INDEX idx_payments_charge ON payments(charge_id);
CREATE INDEX idx_payments_dispute_window ON payments(dispute_deadline) WHERE status = 'auto_accepted';

-- =====================================================================
--  8. КОМУНАЛКА ТА ЛІЧИЛЬНИКИ
-- =====================================================================
-- Довідник постачальників. window_rule — вікно подачі показників:
--   {"from_day": 1, "to_day": 5}
--   {"from_day": 10, "to": "last_working_day"}
--   {"from_days_before_month_end": 2, "to_day_next_month": 3}
CREATE TABLE utility_providers (
    id              serial PRIMARY KEY,
    code            text NOT NULL UNIQUE,
    name            text NOT NULL,
    meter_kinds     meter_kind[] NOT NULL,
    window_rule     jsonb NOT NULL DEFAULT '{}',
    cabinet_url     text,
    payment_url     text,
    city_code       text NOT NULL REFERENCES cities(code)
);

CREATE TABLE meters (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id         uuid NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    kind                meter_kind NOT NULL,
    provider_id         int REFERENCES utility_providers(id),
    account_number      text,              -- особовий рахунок
    serial_number       text,
    label               text,              -- 'Кухня', 'Ванна'
    submits             party_role NOT NULL DEFAULT 'tenant',   -- хто подає показники
    pays                party_role NOT NULL DEFAULT 'tenant',   -- хто платить
    tariff              numeric(10,4),     -- ручний тариф для розрахунку, опційно
    is_active           boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_meters_property ON meters(property_id);

CREATE TABLE meter_readings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meter_id        uuid NOT NULL REFERENCES meters(id) ON DELETE CASCADE,
    lease_id        uuid REFERENCES leases(id),
    value           numeric(12,3) NOT NULL CHECK (value >= 0),
    photo_key       text,
    submitted_by    uuid NOT NULL REFERENCES users(id),
    reading_date    date NOT NULL,
    sent_to_provider_at timestamptz,       -- користувач позначив «передав постачальнику»
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_meter_readings_meter ON meter_readings(meter_id, reading_date DESC);

-- =====================================================================
--  9. КОМУНІКАЦІЯ ТА ПОБУТ
-- =====================================================================
-- Розмова прив'язана або до заявки (до оренди), або до оренди.
CREATE TABLE conversations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid REFERENCES leases(id) ON DELETE CASCADE,
    application_id  uuid REFERENCES applications(id) ON DELETE CASCADE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((lease_id IS NOT NULL) <> (application_id IS NOT NULL))
);
CREATE UNIQUE INDEX uq_conversation_lease ON conversations(lease_id) WHERE lease_id IS NOT NULL;
CREATE UNIQUE INDEX uq_conversation_app   ON conversations(application_id) WHERE application_id IS NOT NULL;

CREATE TABLE messages (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id     uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id           uuid REFERENCES users(id),   -- NULL = системне повідомлення
    body                text,
    attachment_key      text,
    attachment_type     text,
    system_code         text,                        -- системне повідомлення (FR-CHT-2), текст — у локалізації
    created_at          timestamptz NOT NULL DEFAULT now(),
    edited_at           timestamptz,
    deleted_at          timestamptz,
    CHECK (body IS NOT NULL OR attachment_key IS NOT NULL OR system_code IS NOT NULL)
);
CREATE INDEX idx_messages_conv ON messages(conversation_id, id);

-- Чат живе в застосунку (ADR 0004): достатньо «до якого повідомлення прочитано».
CREATE TABLE conversation_reads (
    conversation_id      uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_read_message_id bigint NOT NULL DEFAULT 0,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, user_id)
);

-- Заявки на ремонт / поломки.
CREATE TABLE maintenance_requests (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    created_by      uuid NOT NULL REFERENCES users(id),
    title           text NOT NULL,
    description     text,
    photo_keys      text[] NOT NULL DEFAULT '{}',
    status          request_status NOT NULL DEFAULT 'open',
    resolved_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_maintenance_lease ON maintenance_requests(lease_id, status);
CREATE TRIGGER trg_maintenance_updated BEFORE UPDATE ON maintenance_requests
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Візит власника за попереднім погодженням (біль «приходить без попередження»).
CREATE TABLE visit_requests (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        uuid NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    proposed_by     uuid NOT NULL REFERENCES users(id),
    starts_at       timestamptz NOT NULL,
    reason          text,
    status          visit_status NOT NULL DEFAULT 'proposed',
    responded_by    uuid REFERENCES users(id),
    responded_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_visits_lease ON visit_requests(lease_id, starts_at);

-- Налаштування сповіщень: глобально (lease_id NULL) або для конкретної оренди.
CREATE TABLE notification_preferences (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lease_id    uuid REFERENCES leases(id) ON DELETE CASCADE,
    type        text NOT NULL,        -- 'rent_reminder', 'meter_reminder', 'chat_message', 'new_match', ...
    channel     text NOT NULL DEFAULT 'telegram',
    enabled     boolean NOT NULL DEFAULT true,
    params      jsonb NOT NULL DEFAULT '{}',   -- {"days_before": 3, "quiet_hours": [22, 8]}
    UNIQUE NULLS NOT DISTINCT (user_id, lease_id, type, channel)
);

-- =====================================================================
--  10. ДОВІРА: ВІДГУКИ, СКАРГИ, МОДЕРАЦІЯ, БАНИ
-- =====================================================================
CREATE TABLE reviews (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id  uuid REFERENCES applications(id) ON DELETE CASCADE,
    lease_id        uuid REFERENCES leases(id) ON DELETE CASCADE,
    author_id       uuid NOT NULL REFERENCES users(id),
    target_id       uuid NOT NULL REFERENCES users(id),
    target_role     party_role NOT NULL,
    rating          smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body            text,
    target_reply    text,                  -- право на відповідь
    is_hidden       boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((application_id IS NOT NULL) <> (lease_id IS NOT NULL)),
    CHECK (author_id <> target_id)
);
CREATE UNIQUE INDEX uq_review_app   ON reviews(application_id, author_id) WHERE application_id IS NOT NULL;
CREATE UNIQUE INDEX uq_review_lease ON reviews(lease_id, author_id, target_id) WHERE lease_id IS NOT NULL;
CREATE INDEX idx_reviews_target ON reviews(target_id) WHERE NOT is_hidden;

CREATE TABLE complaints (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reporter_id     uuid NOT NULL REFERENCES users(id),
    target_user_id  uuid NOT NULL REFERENCES users(id),
    listing_id      uuid REFERENCES listings(id) ON DELETE SET NULL,
    application_id  uuid REFERENCES applications(id) ON DELETE SET NULL,
    lease_id        uuid REFERENCES leases(id) ON DELETE SET NULL,
    reason          complaint_reason NOT NULL,
    details         text,
    evidence_keys   text[] NOT NULL DEFAULT '{}',
    status          complaint_status NOT NULL DEFAULT 'new',
    reviewed_by     uuid REFERENCES users(id),
    reviewed_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (reporter_id <> target_user_id)
);
CREATE UNIQUE INDEX uq_open_complaint ON complaints(reporter_id, target_user_id) WHERE status = 'new';
CREATE INDEX idx_complaints_target ON complaints(target_user_id, status);
CREATE INDEX idx_complaints_queue  ON complaints(created_at) WHERE status = 'new';

CREATE TABLE risk_signals (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    signal_type text NOT NULL,   -- 'many_addresses', 'duplicate_photos', 'phone_in_text',
                                 -- 'commission_keywords', 'reregistration_attempt', 'signer_name_mismatch',
                                 -- 'duplicate_address'
    weight      smallint NOT NULL DEFAULT 1,
    details     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_risk_signals_user ON risk_signals(user_id, created_at);

CREATE TABLE moderation_cases (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    target_user_id  uuid NOT NULL REFERENCES users(id),
    trigger         case_trigger NOT NULL,
    status          case_status  NOT NULL DEFAULT 'open',
    assigned_to     uuid REFERENCES users(id),
    resolution      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz
);
CREATE UNIQUE INDEX uq_open_case_per_user
    ON moderation_cases(target_user_id) WHERE status IN ('open', 'in_progress');

CREATE TABLE bans (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid REFERENCES users(id),
    identity_hash   text,
    phone_hash      text,
    telegram_uid    text,
    reason          text NOT NULL,
    case_id         uuid REFERENCES moderation_cases(id),
    created_by      uuid REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz,
    lifted_at       timestamptz,
    CHECK (identity_hash IS NOT NULL OR phone_hash IS NOT NULL OR telegram_uid IS NOT NULL)
);
CREATE INDEX idx_bans_identity ON bans(identity_hash) WHERE identity_hash IS NOT NULL;
CREATE INDEX idx_bans_phone    ON bans(phone_hash)    WHERE phone_hash    IS NOT NULL;
CREATE INDEX idx_bans_telegram ON bans(telegram_uid)  WHERE telegram_uid  IS NOT NULL;

-- Журнал дій модераторів/адмінів (хто кого призначив, забанив, верифікував).
CREATE TABLE audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id    uuid REFERENCES users(id),
    action      text NOT NULL,        -- 'role.grant', 'ownership.approve', 'ban.create', ...
    entity_type text NOT NULL,
    entity_id   text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);

-- =====================================================================
--  11. СЛУЖБОВЕ
-- =====================================================================
CREATE TABLE notifications (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type        text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}',
    channel     text NOT NULL DEFAULT 'telegram',
    dedupe_key  text UNIQUE,          -- 'rent_reminder:<charge_id>:d-3' — не слати двічі
    send_after  timestamptz NOT NULL DEFAULT now(),
    sent_at     timestamptz,
    read_at     timestamptz,
    attempts    smallint NOT NULL DEFAULT 0,
    last_error  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_notifications_pending ON notifications(send_after) WHERE sent_at IS NULL;

CREATE TABLE events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
    name        text NOT NULL,
    props       jsonb NOT NULL DEFAULT '{}',
    source      text NOT NULL DEFAULT 'telegram_bot',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_events_name_time ON events(name, created_at);

-- =====================================================================
--  12. ПРЕДСТАВЛЕННЯ
-- =====================================================================
CREATE VIEW user_reputation AS
SELECT
    u.id AS user_id,
    COUNT(r.id) FILTER (WHERE r.target_role = 'tenant')                 AS reviews_as_tenant,
    ROUND(AVG(r.rating) FILTER (WHERE r.target_role = 'tenant'), 2)     AS rating_as_tenant,
    COUNT(r.id) FILTER (WHERE r.target_role = 'landlord')               AS reviews_as_landlord,
    ROUND(AVG(r.rating) FILTER (WHERE r.target_role = 'landlord'), 2)   AS rating_as_landlord,
    (SELECT COUNT(DISTINCT c.reporter_id)
       FROM complaints c
      WHERE c.target_user_id = u.id AND c.status <> 'dismissed')       AS complainants_count
FROM users u
LEFT JOIN reviews r ON r.target_id = u.id AND NOT r.is_hidden
GROUP BY u.id;

-- =====================================================================
--  13. ПОЧАТКОВІ ДАНІ
-- =====================================================================
INSERT INTO districts (name) VALUES
    ('Галицький'), ('Залізничний'), ('Личаківський'),
    ('Сихівський'), ('Франківський'), ('Шевченківський');

INSERT INTO amenities (code, name_uk) VALUES
    ('furniture',          'Меблі'),
    ('appliances',         'Побутова техніка'),
    ('washing_machine',    'Пральна машина'),
    ('dishwasher',         'Посудомийна машина'),
    ('air_conditioner',    'Кондиціонер'),
    ('boiler',             'Бойлер'),
    ('autonomous_heating', 'Автономне опалення'),
    ('balcony',            'Балкон'),
    ('elevator',           'Ліфт'),
    ('parking',            'Паркомісце'),
    ('internet',           'Інтернет'),
    ('backup_power',       'Резервне живлення (генератор / EcoFlow / інвертор)'),
    ('shelter_nearby',     'Укриття поруч');

-- Вікна подачі показників — станом на листопад 2026, перевіряти перед запуском.
INSERT INTO cities (code, name, region, status, sort_order) VALUES
    ('lviv', 'Львів', 'Львівська область', 'active', 1);

INSERT INTO utility_providers (code, name, meter_kinds, window_rule, cabinet_url, city_code) VALUES
    ('lviv_vodokanal', 'ЛМКП «Львівводоканал»', '{water_cold}',
        '{"from_day": 10, "to": "last_working_day"}', 'https://infolviv.com.ua', 'lviv'),
    ('lviv_teploenergo', 'ЛМКП «Львівтеплоенерго»', '{water_hot,heat}',
        '{"from_day": 10, "to": "last_working_day"}', 'https://lte.leotel.com.ua', 'lviv'),
    ('lviv_energozbut', '«Львівенергозбут»', '{electricity}',
        '{"from_days_before_month_end": 2, "to_day_next_month": 3}', 'https://lez.com.ua', 'lviv'),
    ('gazmerezhi_lviv', '«Газмережі», Львівська філія', '{gas}',
        '{"from_day": 1, "to_day": 5}', 'https://my.grmu.com.ua', 'lviv');
