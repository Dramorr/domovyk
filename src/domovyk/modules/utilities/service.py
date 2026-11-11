"""Публічний інтерфейс модуля utilities: лічильники й показники (M5, частина 3).

FR-UTL-1..4. Власник додає лічильники об'єкта; той, хто подає показники, отримує
нагадування у вікно свого постачальника (`utility_providers.window_rule`), подає число
з фото, бачить спожите й переходить у кабінет постачальника, щоб передати показник
туди сам. Кабінети постачальників не скрейпимо (FR-UTL-6, правило 8).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from domovyk.adapters.images.base import ImageProcessor, ImageRejected
from domovyk.adapters.storage.base import FileStorage
from domovyk.core.clock import Clock, to_kyiv
from domovyk.core.errors import Conflict, InvalidInput, NotFound, ServiceUnavailable
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.contracts import PartyRole
from domovyk.domain.leases import LeaseStatus
from domovyk.domain.utilities import (
    ACCOUNT_MAX,
    LABEL_MAX,
    PHOTO_MAX_BYTES,
    Window,
    check_not_less,
    clean_reading,
    clean_tariff,
    clean_text,
    current_or_next_window,
    estimate,
    reminder_stage,
)
from domovyk.modules.leases.service import LeasesService
from domovyk.modules.notifications.service import NotificationsService, NotificationType
from domovyk.modules.properties.schemas import PropertyOut
from domovyk.modules.properties.service import PropertiesService
from domovyk.modules.users.service import UsersService
from domovyk.modules.utilities.repository import (
    MeterRecord,
    NewMeter,
    NewReading,
    ProviderRecord,
    ReadingRecord,
    UtilitiesRepository,
)
from domovyk.modules.utilities.schemas import (
    MeterIn,
    MeterOut,
    MetersOverview,
    ProviderOut,
    ReadingOut,
    ReadingResult,
    WindowOut,
)

__all__ = [
    "MeterIn",
    "MeterOut",
    "MetersOverview",
    "ProviderOut",
    "ReadingOut",
    "ReadingResult",
    "UtilitiesConfig",
    "UtilitiesService",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UtilitiesConfig:
    max_meters: int = 20
    history_limit: int = 24
    # Орендар бачить показники з дати заїзду мінус N днів (щоб був «стартовий»).
    tenant_history_days_before: int = 40
    # Свій останній показник орендар може видалити протягом N годин (помилився цифрою).
    delete_window_hours: int = 48
    photo_max_bytes: int = PHOTO_MAX_BYTES


# Кому нагадувати: (користувач, жива оренда або None) і група (+ етап, вікно).
_Recipient = tuple[uuid.UUID, uuid.UUID | None]
_GroupKey = tuple[uuid.UUID, uuid.UUID | None, str, Window]


@dataclass(frozen=True)
class _Viewer:
    user_id: uuid.UUID
    role: PartyRole
    prop: PropertyOut
    lease_id: uuid.UUID | None  # жива оренда, до якої прив'язуємо показники
    since: date | None  # з якої дати бачить показники (орендар)
    until: date | None  # до якої (орендар завершеної оренди)
    can_submit: bool


class UtilitiesService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], UtilitiesRepository],
        users: UsersService,
        properties: PropertiesService,
        leases: LeasesService,
        notifications: NotificationsService,
        storage: FileStorage,
        clock: Clock,
        config: UtilitiesConfig,
        images: ImageProcessor | None = None,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._properties = properties
        self._leases = leases
        self._notifications = notifications
        self._storage = storage
        self._clock = clock
        self._config = config
        self._images = images

    # -------------------------------------------------------------- читання

    async def for_property(self, telegram_uid: int, property_id: uuid.UUID) -> MetersOverview:
        """UTL-LIST з картки об'єкта — лише власник."""
        return await self._overview(await self._as_owner(telegram_uid, property_id))

    async def for_lease(self, telegram_uid: int, lease_id: uuid.UUID) -> MetersOverview:
        """UTL-LIST з картки оренди — власник або учасник оренди."""
        info = await self._leases.for_contract(telegram_uid, lease_id)
        live = info.lease.status in (LeaseStatus.ACTIVE, LeaseStatus.ENDING)
        if info.viewer_user_id is None:
            raise NotFound("Оренду не знайдено")
        if info.lease.my_role == "landlord":
            viewer = _Viewer(
                info.viewer_user_id,
                PartyRole.LANDLORD,
                info.property,
                lease_id if live else None,
                None,
                None,
                True,
            )
        else:
            since = info.lease.start_date - timedelta(days=self._config.tenant_history_days_before)
            until = (
                None
                if live or info.lease.status is not LeaseStatus.ENDED
                else (
                    info.lease.termination.date if info.lease.termination else info.lease.end_date
                )
            )
            viewer = _Viewer(
                info.viewer_user_id,
                PartyRole.TENANT,
                info.property,
                lease_id if live else None,
                since,
                until,
                live,
            )
        return await self._overview(viewer)

    async def reading_photo(self, telegram_uid: int, reading_id: uuid.UUID) -> bytes:
        reading, _meter, viewer = await self._reading_ctx(telegram_uid, reading_id)
        if reading.photo_key is None or not _visible(reading, viewer):
            raise NotFound("Фото немає")
        return await self._storage.get(reading.photo_key)

    # --------------------------------------------------- власник: лічильники

    async def add_meter(
        self, telegram_uid: int, property_id: uuid.UUID, data: MeterIn
    ) -> MetersOverview:
        """FR-UTL-1: тип, постачальник з довідника міста, рахунок, хто подає і хто платить."""
        viewer = await self._as_owner(telegram_uid, property_id)
        initial = clean_reading(data.initial_value) if data.initial_value is not None else None
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            if len([m for m in await repo.meters(property_id) if m.is_active]) >= (
                self._config.max_meters
            ):
                raise InvalidInput("Забагато лічильників", field="kind", reason="too_many")
            await self._check_provider(repo, viewer.prop, data)
            meter_id = await repo.add_meter(
                NewMeter(
                    property_id=property_id,
                    kind=data.kind,
                    provider_id=data.provider_id,
                    account_number=clean_text(data.account_number, "account_number", ACCOUNT_MAX),
                    label=clean_text(data.label, "label", LABEL_MAX),
                    submits=data.submits,
                    pays=data.pays,
                    tariff=clean_tariff(data.tariff),
                )
            )
            if initial is not None:
                await repo.add_reading(self._new_reading(meter_id, viewer, initial, None))
        return await self._overview(viewer)

    async def update_meter(
        self, telegram_uid: int, meter_id: uuid.UUID, data: MeterIn
    ) -> MetersOverview:
        meter = await self._meter(meter_id)
        viewer = await self._as_owner(telegram_uid, meter.property_id)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            if data.kind is not meter.kind and await repo.readings([meter_id]):
                raise Conflict("Тип лічильника з показниками не змінюється", field="kind")
            await self._check_provider(repo, viewer.prop, data)
            await repo.update_meter(
                meter_id,
                kind=data.kind,
                provider_id=data.provider_id,
                account_number=clean_text(data.account_number, "account_number", ACCOUNT_MAX),
                label=clean_text(data.label, "label", LABEL_MAX),
                submits=data.submits,
                pays=data.pays,
                tariff=clean_tariff(data.tariff),
                is_active=True,
            )
        return await self._overview(viewer)

    async def remove_meter(self, telegram_uid: int, meter_id: uuid.UUID) -> MetersOverview:
        """Без показників — видаляємо; з показниками — вимикаємо (історія лишається обом)."""
        meter = await self._meter(meter_id)
        viewer = await self._as_owner(telegram_uid, meter.property_id)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            if await repo.readings([meter_id]):
                await repo.update_meter(meter_id, is_active=False)
            else:
                await repo.delete_meter(meter_id)
        return await self._overview(viewer)

    # ------------------------------------------------------------- показники

    async def submit_reading(
        self,
        telegram_uid: int,
        meter_id: uuid.UUID,
        raw_value: Decimal | str,
        photo: bytes | None = None,
    ) -> ReadingResult:
        """FR-UTL-3: число з табло (+ фото); не менше попереднього."""
        meter = await self._meter(meter_id)
        if not meter.is_active:
            raise Conflict("Лічильник вимкнено", field="meter")
        viewer = await self._for_meter(telegram_uid, meter)
        if not viewer.can_submit:
            raise Conflict("Оренда не активна", field="status")
        value = clean_reading(raw_value)
        async with self._tx.begin() as tx:
            history = await self._repo(tx).readings([meter_id])
        check_not_less(history[0].value if history else None, value)
        image = await self._prepare_photo(photo) if photo else None

        key = f"meters/{meter_id}/{uuid.uuid4()}.jpg" if image is not None else None
        if key is not None and image is not None:
            await self._storage.put(key, image)
        try:
            async with self._tx.begin() as tx:
                repo = self._repo(tx)
                latest = await repo.readings([meter_id])
                check_not_less(latest[0].value if latest else None, value)
                reading_id = await repo.add_reading(self._new_reading(meter_id, viewer, value, key))
                provider = await repo.provider(meter.provider_id) if meter.provider_id else None
        except Exception:
            if key is not None:
                await self._storage.delete(key)
            raise
        return await self._result(viewer, meter_id, reading_id, provider)

    async def mark_sent(self, telegram_uid: int, reading_id: uuid.UUID) -> ReadingResult:
        """«Передав постачальнику» — позначка для себе й для власника."""
        reading, meter, viewer = await self._reading_ctx(telegram_uid, reading_id)
        if reading.submitted_by != viewer.user_id and viewer.role is not PartyRole.LANDLORD:
            raise NotFound("Показник не знайдено")
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            if reading.sent_to_provider_at is None:
                await repo.mark_sent(reading_id, self._clock.now())
            provider = await repo.provider(meter.provider_id) if meter.provider_id else None
        return await self._result(viewer, meter.id, reading_id, provider)

    async def delete_reading(self, telegram_uid: int, reading_id: uuid.UUID) -> MetersOverview:
        """Помилився цифрою: видалити останній показник (свій — протягом N годин)."""
        reading, _meter, viewer = await self._reading_ctx(telegram_uid, reading_id)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            history = await repo.readings([reading.meter_id])
            if not self._can_delete(reading, history, viewer):
                raise Conflict("Цей показник уже не можна видалити", field="reading")
            await repo.delete_reading(reading_id)
        if reading.photo_key:
            await self._storage.delete(reading.photo_key)
        return await self._overview(viewer)

    # ------------------------------------------------------ фонова задача

    async def run_reminders(self) -> int:
        """FR-UTL-2: нагадати тому, хто подає, — у вікні постачальника і в останні його дні."""
        today = to_kyiv(self._clock.now()).date()
        async with self._tx.begin() as tx:
            property_ids = await self._repo(tx).active_meter_property_ids()
        queued = 0
        for property_id in property_ids:
            try:
                queued += await self._remind_property(property_id, today)
            except Exception:  # один збійний об'єкт не зупиняє решту
                log.exception("Нагадування про показники: об'єкт %s", property_id)
        return queued

    async def _remind_property(self, property_id: uuid.UUID, today: date) -> int:
        prop = await self._properties.property_by_id(property_id)
        if prop is None:
            return 0
        lease_ids = await self._leases.live_lease_ids([u.id for u in prop.units])
        queued = 0
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            # Хто подає за кожну живу оренду: відповідальний орендар (з дати заїзду).
            tenants: list[_Recipient] = []
            for live_id in lease_ids:
                info = await self._leases.for_contract_by_id(tx, live_id)
                if info.responsible_user_id and info.lease.start_date <= today:
                    tenants.append((info.responsible_user_id, live_id))
            groups: dict[_GroupKey, list[tuple[MeterRecord, ProviderRecord]]] = {}
            providers: dict[int, ProviderRecord | None] = {}
            for meter in await repo.meters(property_id):
                if not meter.is_active or meter.provider_id is None:
                    continue
                if meter.provider_id not in providers:
                    providers[meter.provider_id] = await repo.provider(meter.provider_id)
                provider = providers[meter.provider_id]
                if provider is None:
                    continue
                window = current_or_next_window(provider.window_rule, today)
                stage = reminder_stage(window, today)
                if stage is None or window is None:
                    continue
                history = await repo.readings([meter.id])
                if _submitted_in(window, meter, history, prop.owner_user_id):
                    continue
                recipients: list[_Recipient] = (
                    [(prop.owner_user_id, lease_ids[0] if lease_ids else None)]
                    if meter.submits is PartyRole.LANDLORD
                    else tenants
                )
                for recipient in recipients:
                    groups.setdefault((*recipient, stage, window), []).append((meter, provider))
            for (user_id, lease_id, group_stage, window), items in groups.items():
                meters = sorted(items, key=lambda mp: str(mp[0].id))
                payload: dict[str, Any] = {
                    "event": group_stage,
                    "property_id": str(property_id),
                    "lease_id": str(lease_id) if lease_id else "",
                    "street": prop.street,
                    "house_number": prop.house_number,
                    "apartment": prop.apartment or "",
                    "kinds": [m.kind.value for m, _ in meters],
                    "providers": sorted({p.name for _, p in meters}),
                    "window_start": window.start.isoformat(),
                    "window_end": window.end.isoformat(),
                }
                stamp = ",".join(str(m.id)[:8] for m, _ in meters)
                key = f"meters:{property_id}:{user_id}:{window.start}:{group_stage}:{stamp}"
                if lease_id is not None:
                    sent = await self._notifications.enqueue_for_lease(
                        tx,
                        user_id,
                        lease_id,
                        NotificationType.METER_REMINDER,
                        payload,
                        dedupe_key=key,
                    )
                else:
                    sent = await self._notifications.enqueue(
                        tx, user_id, NotificationType.METER_REMINDER, payload, dedupe_key=key
                    )
                queued += int(sent)
        return queued

    # ------------------------------------------------------------ службове

    async def _as_owner(self, telegram_uid: int, property_id: uuid.UUID) -> _Viewer:
        prop = await self._properties.get_mine(telegram_uid, property_id)
        lease_ids = await self._leases.live_lease_ids([u.id for u in prop.units])
        return _Viewer(
            prop.owner_user_id,
            PartyRole.LANDLORD,
            prop,
            lease_ids[0] if lease_ids else None,
            None,
            None,
            True,
        )

    async def _for_meter(self, telegram_uid: int, meter: MeterRecord) -> _Viewer:
        """Власник об'єкта або орендар живої оренди на ньому. Інші — «не знайдено»."""
        try:
            return await self._as_owner(telegram_uid, meter.property_id)
        except NotFound:
            pass
        prop = await self._properties.property_by_id(meter.property_id)
        if prop is None:
            raise NotFound("Лічильник не знайдено")
        for lease_id in await self._leases.live_lease_ids([u.id for u in prop.units]):
            try:
                info = await self._leases.for_contract(telegram_uid, lease_id)
            except NotFound:
                continue
            if info.lease.my_role != "landlord" and info.viewer_user_id is not None:
                since = info.lease.start_date - timedelta(
                    days=self._config.tenant_history_days_before
                )
                return _Viewer(
                    info.viewer_user_id, PartyRole.TENANT, prop, lease_id, since, None, True
                )
        raise NotFound("Лічильник не знайдено")

    async def _reading_ctx(
        self, telegram_uid: int, reading_id: uuid.UUID
    ) -> tuple[ReadingRecord, MeterRecord, _Viewer]:
        async with self._tx.begin() as tx:
            reading = await self._repo(tx).reading(reading_id)
        if reading is None:
            raise NotFound("Показник не знайдено")
        meter = await self._meter(reading.meter_id)
        viewer = await self._for_meter(telegram_uid, meter)
        if not _visible(reading, viewer):
            raise NotFound("Показник не знайдено")
        return reading, meter, viewer

    async def _meter(self, meter_id: uuid.UUID) -> MeterRecord:
        async with self._tx.begin() as tx:
            meter = await self._repo(tx).meter(meter_id)
        if meter is None:
            raise NotFound("Лічильник не знайдено")
        return meter

    @staticmethod
    async def _check_provider(repo: UtilitiesRepository, prop: PropertyOut, data: MeterIn) -> None:
        if data.provider_id is None:
            return
        provider = await repo.provider(data.provider_id)
        if provider is None or provider.city_code != prop.city_code:
            raise InvalidInput("Невідомий постачальник", field="provider_id")
        if data.kind not in provider.meter_kinds:
            raise InvalidInput("Постачальник не обслуговує цей лічильник", field="provider_id")

    async def _prepare_photo(self, data: bytes) -> bytes:
        if self._images is None:
            raise ServiceUnavailable("Обробка фото не налаштована")
        if len(data) > self._config.photo_max_bytes:
            raise InvalidInput("Фото завелике", field="photo", reason="too_big")
        try:
            return (await self._images.prepare(data)).data
        except ImageRejected as e:
            raise InvalidInput("Це не фото", field="photo", reason=e.reason) from None

    def _new_reading(
        self, meter_id: uuid.UUID, viewer: _Viewer, value: Decimal, photo_key: str | None
    ) -> NewReading:
        now = self._clock.now()
        return NewReading(
            meter_id=meter_id,
            lease_id=viewer.lease_id,
            value=value,
            photo_key=photo_key,
            submitted_by=viewer.user_id,
            reading_date=to_kyiv(now).date(),
            created_at=now,
        )

    def _can_delete(
        self, reading: ReadingRecord, history: list[ReadingRecord], viewer: _Viewer
    ) -> bool:
        if not history or history[0].id != reading.id:
            return False  # лише останній — інакше зламається ланцюжок «спожито»
        if viewer.role is PartyRole.LANDLORD:
            return True
        fresh = self._clock.now() - reading.created_at <= timedelta(
            hours=self._config.delete_window_hours
        )
        return reading.submitted_by == viewer.user_id and fresh

    async def _result(
        self,
        viewer: _Viewer,
        meter_id: uuid.UUID,
        reading_id: uuid.UUID,
        provider: ProviderRecord | None,
    ) -> ReadingResult:
        overview = await self._overview(viewer)
        meter = next(m for m in overview.meters if m.id == meter_id)
        reading = next(r for r in meter.readings if r.id == reading_id)
        return ReadingResult(
            meter=meter, reading=reading, cabinet_url=provider.cabinet_url if provider else None
        )

    async def _overview(self, viewer: _Viewer) -> MetersOverview:
        today = to_kyiv(self._clock.now()).date()
        manage = viewer.role is PartyRole.LANDLORD
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            providers = await repo.providers(viewer.prop.city_code)
            meters = [m for m in await repo.meters(viewer.prop.id) if manage or m.is_active]
            readings = await repo.readings([m.id for m in meters])
        by_id = {p.id: p for p in providers}
        names: dict[uuid.UUID, str] = {}
        for r in readings:
            if r.submitted_by not in names:
                user = await self._users.get_by_id(r.submitted_by)
                names[r.submitted_by] = user.display_name if user else ""
        out: list[MeterOut] = []
        for m in meters:
            history = [r for r in readings if r.meter_id == m.id]
            provider = by_id.get(m.provider_id) if m.provider_id is not None else None
            window = current_or_next_window(provider.window_rule, today) if provider else None
            items: list[ReadingOut] = []
            for i, r in enumerate(history):
                if not _visible(r, viewer):
                    continue
                prev = history[i + 1] if i + 1 < len(history) else None
                consumed = r.value - prev.value if prev is not None else None
                items.append(
                    ReadingOut(
                        id=r.id,
                        value=r.value,
                        consumed=consumed,
                        estimate=estimate(consumed, m.tariff),
                        reading_date=r.reading_date,
                        submitted_by_name=names.get(r.submitted_by, ""),
                        mine=r.submitted_by == viewer.user_id,
                        has_photo=r.photo_key is not None,
                        sent_to_provider_at=r.sent_to_provider_at,
                        can_delete=self._can_delete(r, history, viewer),
                    )
                )
                if len(items) >= self._config.history_limit:
                    break
            is_open = window is not None and window.contains(today)
            out.append(
                MeterOut(
                    id=m.id,
                    kind=m.kind,
                    label=m.label,
                    provider=_provider_out(provider) if provider else None,
                    account_number=m.account_number,
                    submits=m.submits,
                    pays=m.pays,
                    tariff=m.tariff,
                    is_active=m.is_active,
                    window=WindowOut(start=window.start, end=window.end, open=is_open)
                    if window
                    else None,
                    due=bool(
                        m.is_active
                        and window is not None
                        and is_open
                        and not _submitted_in(window, m, history, viewer.prop.owner_user_id)
                    ),
                    i_submit=m.submits is viewer.role,
                    last=items[0] if items else None,
                    readings=items,
                )
            )
        return MetersOverview(
            property_id=viewer.prop.id,
            lease_id=viewer.lease_id,
            my_role=viewer.role,
            can_manage=manage,
            can_submit=viewer.can_submit,
            meters=out,
            providers=[_provider_out(p) for p in providers] if manage else [],
        )


def _submitted_in(
    window: Window, meter: MeterRecord, history: list[ReadingRecord], owner_id: uuid.UUID
) -> bool:
    """Чи подала показник у цьому вікні та сторона, що подає (стартовий від власника не рахуємо)."""
    by_owner = meter.submits is PartyRole.LANDLORD
    return any(
        window.contains(r.reading_date) and (r.submitted_by == owner_id) == by_owner
        for r in history
    )


def _visible(reading: ReadingRecord, viewer: _Viewer) -> bool:
    if viewer.since is not None and reading.reading_date < viewer.since:
        return False
    return viewer.until is None or reading.reading_date <= viewer.until


def _provider_out(p: ProviderRecord) -> ProviderOut:
    return ProviderOut(
        id=p.id,
        name=p.name,
        meter_kinds=p.meter_kinds,
        cabinet_url=p.cabinet_url,
        payment_url=p.payment_url,
    )
