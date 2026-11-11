"""Доступ до utility_providers, meters і meter_readings. Лише всередині модуля utilities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import delete, select, update

from domovyk.core.tx import Tx, session_of
from domovyk.domain.contracts import PartyRole
from domovyk.domain.utilities import MeterKind
from domovyk.modules.utilities.models import MeterRow, ProviderRow, ReadingRow


@dataclass(frozen=True)
class ProviderRecord:
    id: int
    code: str
    name: str
    meter_kinds: list[MeterKind]
    window_rule: dict[str, Any]
    cabinet_url: str | None
    payment_url: str | None
    city_code: str


@dataclass(frozen=True)
class MeterRecord:
    id: uuid.UUID
    property_id: uuid.UUID
    kind: MeterKind
    provider_id: int | None
    account_number: str | None
    label: str | None
    submits: PartyRole
    pays: PartyRole
    tariff: Decimal | None
    is_active: bool
    created_at: datetime
    serial_number: str | None = None


@dataclass(frozen=True)
class NewMeter:
    property_id: uuid.UUID
    kind: MeterKind
    provider_id: int | None
    account_number: str | None
    label: str | None
    submits: PartyRole
    pays: PartyRole
    tariff: Decimal | None


@dataclass(frozen=True)
class ReadingRecord:
    id: uuid.UUID
    meter_id: uuid.UUID
    lease_id: uuid.UUID | None
    value: Decimal
    photo_key: str | None
    submitted_by: uuid.UUID
    reading_date: date
    sent_to_provider_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class NewReading:
    meter_id: uuid.UUID
    lease_id: uuid.UUID | None
    value: Decimal
    photo_key: str | None
    submitted_by: uuid.UUID
    reading_date: date
    created_at: datetime


class UtilitiesRepository(Protocol):
    async def providers(self, city_code: str) -> list[ProviderRecord]: ...
    async def provider(self, provider_id: int) -> ProviderRecord | None: ...
    async def meters(self, property_id: uuid.UUID) -> list[MeterRecord]: ...
    async def meter(self, meter_id: uuid.UUID) -> MeterRecord | None: ...
    async def active_meter_property_ids(self) -> list[uuid.UUID]: ...
    async def add_meter(self, data: NewMeter) -> uuid.UUID: ...
    async def update_meter(self, meter_id: uuid.UUID, **values: Any) -> None: ...
    async def delete_meter(self, meter_id: uuid.UUID) -> None: ...
    async def readings(self, meter_ids: list[uuid.UUID]) -> list[ReadingRecord]:
        """Новіші спершу (дата, потім час подачі)."""
        ...

    async def reading(self, reading_id: uuid.UUID) -> ReadingRecord | None: ...
    async def add_reading(self, data: NewReading) -> uuid.UUID: ...
    async def mark_sent(self, reading_id: uuid.UUID, at: datetime) -> None: ...
    async def delete_reading(self, reading_id: uuid.UUID) -> None: ...


def _provider(r: ProviderRow) -> ProviderRecord:
    return ProviderRecord(
        id=r.id,
        code=r.code,
        name=r.name,
        meter_kinds=[MeterKind(k) for k in r.meter_kinds],
        window_rule=dict(r.window_rule or {}),
        cabinet_url=r.cabinet_url,
        payment_url=r.payment_url,
        city_code=r.city_code,
    )


def _meter(r: MeterRow) -> MeterRecord:
    return MeterRecord(
        id=r.id,
        property_id=r.property_id,
        kind=r.kind,
        provider_id=r.provider_id,
        account_number=r.account_number,
        label=r.label,
        submits=r.submits,
        pays=r.pays,
        tariff=r.tariff,
        is_active=r.is_active,
        created_at=r.created_at,
        serial_number=r.serial_number,
    )


def _reading(r: ReadingRow) -> ReadingRecord:
    return ReadingRecord(
        id=r.id,
        meter_id=r.meter_id,
        lease_id=r.lease_id,
        value=r.value,
        photo_key=r.photo_key,
        submitted_by=r.submitted_by,
        reading_date=r.reading_date,
        sent_to_provider_at=r.sent_to_provider_at,
        created_at=r.created_at,
    )


class SqlUtilitiesRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def providers(self, city_code: str) -> list[ProviderRecord]:
        rows = await self._s.scalars(
            select(ProviderRow).where(ProviderRow.city_code == city_code).order_by(ProviderRow.id)
        )
        return [_provider(r) for r in rows]

    async def provider(self, provider_id: int) -> ProviderRecord | None:
        row = await self._s.get(ProviderRow, provider_id)
        return _provider(row) if row else None

    async def meters(self, property_id: uuid.UUID) -> list[MeterRecord]:
        rows = await self._s.scalars(
            select(MeterRow)
            .where(MeterRow.property_id == property_id)
            .order_by(MeterRow.created_at, MeterRow.id)
        )
        return [_meter(r) for r in rows]

    async def meter(self, meter_id: uuid.UUID) -> MeterRecord | None:
        row = await self._s.get(MeterRow, meter_id)
        return _meter(row) if row else None

    async def active_meter_property_ids(self) -> list[uuid.UUID]:
        rows = await self._s.scalars(
            select(MeterRow.property_id).where(MeterRow.is_active.is_(True)).distinct()
        )
        return list(rows)

    async def add_meter(self, data: NewMeter) -> uuid.UUID:
        row = MeterRow(
            property_id=data.property_id,
            kind=data.kind,
            provider_id=data.provider_id,
            account_number=data.account_number,
            label=data.label,
            submits=data.submits,
            pays=data.pays,
            tariff=data.tariff,
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def update_meter(self, meter_id: uuid.UUID, **values: Any) -> None:
        await self._s.execute(update(MeterRow).where(MeterRow.id == meter_id).values(**values))

    async def delete_meter(self, meter_id: uuid.UUID) -> None:
        await self._s.execute(delete(MeterRow).where(MeterRow.id == meter_id))

    async def readings(self, meter_ids: list[uuid.UUID]) -> list[ReadingRecord]:
        if not meter_ids:
            return []
        rows = await self._s.scalars(
            select(ReadingRow)
            .where(ReadingRow.meter_id.in_(meter_ids))
            .order_by(ReadingRow.reading_date.desc(), ReadingRow.created_at.desc())
        )
        return [_reading(r) for r in rows]

    async def reading(self, reading_id: uuid.UUID) -> ReadingRecord | None:
        row = await self._s.get(ReadingRow, reading_id)
        return _reading(row) if row else None

    async def add_reading(self, data: NewReading) -> uuid.UUID:
        row = ReadingRow(
            meter_id=data.meter_id,
            lease_id=data.lease_id,
            value=data.value,
            photo_key=data.photo_key,
            submitted_by=data.submitted_by,
            reading_date=data.reading_date,
            created_at=data.created_at,
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def mark_sent(self, reading_id: uuid.UUID, at: datetime) -> None:
        await self._s.execute(
            update(ReadingRow).where(ReadingRow.id == reading_id).values(sent_to_provider_at=at)
        )

    async def delete_reading(self, reading_id: uuid.UUID) -> None:
        await self._s.execute(delete(ReadingRow).where(ReadingRow.id == reading_id))
