"""Модуль contracts на справжньому Postgres: запити, upsert підпису, обмеження схеми."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.container import Services
from domovyk.core.errors import LeaseLimitReached
from domovyk.domain.leases import LeaseStatus
from domovyk.domain.properties import PropertyKind
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V
from tests.integration.test_leases_db import OWNER, ROOMMATE, TENANT, _setup
from tests.signing_fixtures import cades

pytestmark = pytest.mark.integration


async def test_contract_cycle_db(services: Services, engine: AsyncEngine) -> None:
    s = services
    data = await _setup(s, engine)
    lease, invite = await s.leases.create_lease(OWNER, data)
    await s.leases.accept_invite(TENANT, invite.token)
    await s.leases.accept_terms(TENANT, lease.id)
    await s.contracts.set_legal_name(OWNER, lease.id, "Шевченко Тарас Григорович")
    await s.contracts.set_legal_name(TENANT, lease.id, "Петренко Андрій")
    await s.contracts.set_legal_name(TENANT, lease.id, "Петренко Андрій Іванович")  # upsert
    first = await s.contracts.generate(OWNER, lease.id)
    state = await s.contracts.generate(OWNER, lease.id)  # попередня версія → void
    assert state.contract is not None and first.contract is not None
    assert state.contract.version == 2
    _, pdf = await s.contracts.document(OWNER, state.contract.id)

    cid = state.contract.id
    await s.contracts.upload_signature(TENANT, cid, "a.p7s", cades(pdf, "Петренко Андрій"))
    await s.contracts.upload_signature(TENANT, cid, "b.p7s", cades(pdf, "Петренко Андрій Іванович"))
    await s.contracts.upload_signature(OWNER, cid, "c.p7s", cades(pdf, "Шевченко Тарас Григорович"))
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM contract_signatures")) == 2
        stored = await conn.scalar(
            text("SELECT check_details::text FROM contract_signatures LIMIT 1")
        )
        assert "1234567890" not in stored  # РНОКПП — лише як хеш
        # Другий живий договір на ту саму оренду — частковий унікальний індекс.
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO contracts (lease_id, source, pdf_storage_key, pdf_sha256, status, "
                    "conditions_hash, created_by) SELECT lease_id, 'generated', 'k', 'h', 'sent', "
                    "'c', created_by FROM contracts WHERE status = 'partially_signed'"
                )
            )
        await conn.rollback()

    await s.users.register(ADMIN_TG, "Модератор", terms_version=TERMS_V, privacy_version=PRIVACY_V)
    queue = await s.contracts.review_queue(ADMIN_TG)
    assert len(queue) == 2
    for case in queue:
        await s.contracts.review_signature(ADMIN_TG, case.signature_id, True, None)
    assert (await s.leases.get(OWNER, lease.id)).status is LeaseStatus.ACTIVE
    assert await s.contracts.review_queue(ADMIN_TG) == []


async def test_tax_limit_query_db(services: Services, engine: AsyncEngine) -> None:
    """lease_ids_signed_by на справжній БД: третя оренда з тим самим РНОКПП — блок."""
    s = services
    base = await _setup(s, engine)
    contracts = []
    for i, tenant in enumerate((TENANT, TENANT, ROOMMATE)):
        prop = await s.properties.create_property(
            OWNER,
            PropertyCreate(
                kind=PropertyKind.APARTMENT,
                address=AddressIn(street="вул. Зелена", house_number=str(50 + i)),
                apartment="1",
                rooms=1,
            ),
        )
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE properties SET ownership_status = 'verified' WHERE id = :id"),
                {"id": prop.id},
            )
        lease, invite = await s.leases.create_lease(
            OWNER, base.model_copy(update={"property_id": prop.id})
        )
        await s.leases.accept_invite(tenant, invite.token)
        await s.leases.accept_terms(tenant, lease.id)
        await s.contracts.set_legal_name(OWNER, lease.id, "Шевченко Тарас Григорович")
        await s.contracts.set_legal_name(tenant, lease.id, "Петренко Андрій Іванович")
        state = await s.contracts.generate(OWNER, lease.id)
        assert state.contract is not None
        _, pdf = await s.contracts.document(OWNER, state.contract.id)
        contracts.append((tenant, state.contract.id, pdf))
    for tenant, cid, pdf in contracts[:2]:
        await s.contracts.upload_signature(
            tenant, cid, "t.p7s", cades(pdf, "Петренко Андрій Іванович")
        )
    tenant, cid, pdf = contracts[2]
    with pytest.raises(LeaseLimitReached):
        await s.contracts.upload_signature(
            tenant, cid, "t.p7s", cades(pdf, "Петренко Андрій Іванович")
        )
