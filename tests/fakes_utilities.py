"""Фейковий репозиторій модуля utilities для юніт-тестів. Лише вигадані дані."""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from domovyk.domain.utilities import MeterKind
from domovyk.modules.utilities.repository import (
    MeterRecord,
    NewMeter,
    NewReading,
    ProviderRecord,
    ReadingRecord,
)


def _providers() -> dict[int, ProviderRecord]:
    """Як у міграції 0016 (адреси кабінетів — вигадані для тестів)."""
    items = [
        ProviderRecord(
            1,
            "test_water",
            "Тестводоканал",
            [MeterKind.WATER_COLD],
            {"from_day": 10, "to": "last_working_day"},
            "https://water.example",
            None,
            "lviv",
        ),
        ProviderRecord(
            2,
            "test_power",
            "Тестенергозбут",
            [MeterKind.ELECTRICITY],
            {"from_days_before_month_end": 2, "to_day_next_month": 3},
            "https://power.example",
            None,
            "lviv",
        ),
        ProviderRecord(
            3,
            "test_gas",
            "Тестгаз",
            [MeterKind.GAS],
            {"from_day": 1, "to_day": 5},
            "https://gas.example",
            None,
            "lviv",
        ),
    ]
    return {p.id: p for p in items}


@dataclass
class UtilityDb:
    providers: dict[int, ProviderRecord] = field(default_factory=_providers)
    meters: dict[uuid.UUID, MeterRecord] = field(default_factory=dict)
    readings: dict[uuid.UUID, ReadingRecord] = field(default_factory=dict)


class FakeUtilitiesRepository:
    def __init__(self, db: UtilityDb) -> None:
        self.db = db

    async def providers(self, city_code: str) -> list[ProviderRecord]:
        return [p for p in self.db.providers.values() if p.city_code == city_code]

    async def provider(self, provider_id: int) -> ProviderRecord | None:
        return self.db.providers.get(provider_id)

    async def meters(self, property_id: uuid.UUID) -> list[MeterRecord]:
        items = [m for m in self.db.meters.values() if m.property_id == property_id]
        return sorted(items, key=lambda m: m.created_at)

    async def meter(self, meter_id: uuid.UUID) -> MeterRecord | None:
        return self.db.meters.get(meter_id)

    async def active_meter_property_ids(self) -> list[uuid.UUID]:
        return sorted({m.property_id for m in self.db.meters.values() if m.is_active}, key=str)

    async def add_meter(self, data: NewMeter) -> uuid.UUID:
        mid = uuid.uuid4()
        created = datetime(2026, 11, 1, tzinfo=UTC).replace(microsecond=len(self.db.meters))
        self.db.meters[mid] = MeterRecord(
            id=mid, is_active=True, created_at=created, **dataclasses.asdict(data)
        )
        return mid

    async def update_meter(self, meter_id: uuid.UUID, **values: Any) -> None:
        self.db.meters[meter_id] = dataclasses.replace(self.db.meters[meter_id], **values)

    async def delete_meter(self, meter_id: uuid.UUID) -> None:
        self.db.meters.pop(meter_id, None)

    async def readings(self, meter_ids: list[uuid.UUID]) -> list[ReadingRecord]:
        items = [r for r in self.db.readings.values() if r.meter_id in meter_ids]
        return sorted(items, key=lambda r: (r.reading_date, r.created_at), reverse=True)

    async def reading(self, reading_id: uuid.UUID) -> ReadingRecord | None:
        return self.db.readings.get(reading_id)

    async def add_reading(self, data: NewReading) -> uuid.UUID:
        rid = uuid.uuid4()
        self.db.readings[rid] = ReadingRecord(
            id=rid, sent_to_provider_at=None, **dataclasses.asdict(data)
        )
        return rid

    async def mark_sent(self, reading_id: uuid.UUID, at: datetime) -> None:
        self.db.readings[reading_id] = dataclasses.replace(
            self.db.readings[reading_id], sent_to_provider_at=at
        )

    async def delete_reading(self, reading_id: uuid.UUID) -> None:
        self.db.readings.pop(reading_id, None)
