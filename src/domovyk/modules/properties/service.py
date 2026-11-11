"""Публічний інтерфейс модуля properties: об'єкти, адреси, юніти, верифікація власності.

FR-PRP-1..5. Інші модулі (оголошення, оренди) перевіряють власність лише через
`require_verified_owner` (правило 5).
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import PurePath

from domovyk.adapters.geocoder.base import Geocoder, GeocoderError
from domovyk.adapters.images.base import ImageProcessor, ImageRejected
from domovyk.adapters.notifier.base import Notifier, NotifierError
from domovyk.adapters.storage.base import FileStorage
from domovyk.core.clock import Clock
from domovyk.core.errors import (
    Conflict,
    ContactsInText,
    DuplicateAddress,
    InvalidInput,
    NotFound,
    OwnershipNotVerified,
    PermissionDenied,
    PhoneRequired,
    ProfileIncomplete,
    PropertyAlreadyYours,
    ServiceUnavailable,
    TooManyDocuments,
)
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.properties import (
    OWNERSHIP,
    VERIFICATION,
    OwnershipStatus,
    PropertyKind,
    UnitKind,
    VerificationStatus,
    building_key,
    can_collect_documents,
    clean_owner_name,
    clean_reason,
    clean_room_name,
    clean_short,
    clean_street,
    normalize_apartment,
    normalize_house_number,
    validate_area,
    validate_city_code,
    validate_floor,
    validate_rooms,
)
from domovyk.domain.property_profile import PROFILE_VERSION, completeness, find_contacts
from domovyk.modules.analytics.service import AnalyticsService, EventName, EventSource
from domovyk.modules.moderation.service import AuditAction, ModerationService, RiskSignal
from domovyk.modules.notifications.service import NotificationsService, NotificationType
from domovyk.modules.properties.repository import (
    NewBuilding,
    NewProperty,
    PropertiesRepository,
    StoredPhoto,
)
from domovyk.modules.properties.schemas import (
    AddressCandidate,
    CityOut,
    CompletenessOut,
    OwnershipOut,
    OwnerSummary,
    PhotoOut,
    PropertyCreate,
    PropertyDetails,
    PropertyOut,
    PropertyUpdate,
    QueueItem,
    RoomIn,
    VerificationCase,
    VerificationOut,
)
from domovyk.modules.users.service import UserOut, UsersService

__all__ = [
    "AddressCandidate",
    "CityOut",
    "OwnershipOut",
    "PhotoOut",
    "PropertiesConfig",
    "PropertiesService",
    "PropertyCreate",
    "PropertyDetails",
    "PropertyOut",
    "PropertyUpdate",
    "QueueItem",
    "RoomIn",
    "VerificationCase",
]

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
QUERY_MIN, QUERY_MAX = 3, 120
_FILENAME_SAFE = re.compile(r"[^\w .()-]+", re.UNICODE)


@dataclass(frozen=True)
class PropertiesConfig:
    dev_skip_ownership_check: bool = False  # FR-PRP-5, лише APP_ENV=dev (перевіряє config.py)
    allow_self_review: bool = False  # модератор перевіряє власний об'єкт — лише в dev
    max_documents: int = 5
    max_document_bytes: int = 10 * 1024 * 1024  # OWN-2: PDF до 10 МБ
    document_retention_days: int = 30  # FR-PRP-4, п. 4
    queue_limit: int = 50
    # Рівень заповненості анкети (FR-PRP-8): з якого відсотка «середній» і «високий».
    completeness_medium_from: int = 40
    completeness_high_from: int = 75
    # Мінімальна заповненість анкети для публікації оголошення (FR-PRP-8).
    listing_min_completeness: int = 40
    # Місто за замовчуванням, коли клієнт його не передав (ADR 0008).
    default_city: str = "lviv"
    # Фото об'єкта (FR-PRP-10).
    max_photos: int = 15
    max_photo_bytes: int = 15 * 1024 * 1024


class PropertiesService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], PropertiesRepository],
        users: UsersService,
        moderation: ModerationService,
        analytics: AnalyticsService,
        notifications: NotificationsService,
        geocoder: Geocoder,
        storage: FileStorage,
        clock: Clock,
        config: PropertiesConfig,
        notifier: Notifier | None = None,
        images: ImageProcessor | None = None,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._moderation = moderation
        self._analytics = analytics
        self._notifications = notifications
        self._geocoder = geocoder
        self._storage = storage
        self._clock = clock
        self._config = config
        self._notifier = notifier
        self._images = images
        self._release: Callable[[PropertyOut], Awaitable[bool]] | None = None

    # ----------------------------------------------------------------- адреси

    async def cities(self) -> list[CityOut]:
        """Довідник міст: де Домовик працює (active) і куди йде (soon). ADR 0008."""
        async with self._tx.begin() as tx:
            return await self._repo(tx).list_cities()

    async def has_city(self, code: str) -> bool:
        """Чи є місто в довіднику (для міста в профілі користувача)."""
        return any(c.code == code for c in await self.cities())

    async def _active_city(self, code: str | None) -> CityOut:
        code = validate_city_code(code or self._config.default_city)
        async with self._tx.begin() as tx:
            city = await self._repo(tx).get_city(code)
        if city is None or city.status != "active":
            raise InvalidInput("У цьому місті Домовик поки не працює", field="city")
        return city

    async def search_address(
        self, telegram_uid: int, query: str, city_code: str | None = None
    ) -> list[AddressCandidate]:
        """PRP-NEW крок 2: знайти будинок у вибраному місті (FR-PRP-2)."""
        await self.require_owner_account(telegram_uid)
        city = await self._active_city(city_code)
        query = " ".join(query.split())
        if not QUERY_MIN <= len(query) <= QUERY_MAX:
            raise InvalidInput("Введи вулицю й номер будинку", field="query")
        try:
            found = await self._geocoder.search(query, city=city.name)
        except GeocoderError as exc:
            log.warning("Геокодер недоступний: %s", exc)
            raise ServiceUnavailable("Пошук адрес тимчасово не працює") from exc
        return [
            AddressCandidate(
                city_code=city.code,
                city=city.name,
                street=a.street,
                house_number=a.house_number,
                district=a.district,
                lat=a.lat,
                lon=a.lon,
                place_id=a.place_id,
            )
            for a in found
            if _valid_house(a.house_number)  # «12;14», «12-14» тощо — не один будинок
        ]

    # ---------------------------------------------------------------- об'єкти

    async def require_owner_account(self, telegram_uid: int) -> UserOut:
        """Активний акаунт з підтвердженим телефоном — без нього об'єкт не створити."""
        user = await self._users.require_active(telegram_uid)
        if not user.has_phone:
            raise PhoneRequired("Спершу підтверди номер телефону")
        return user

    async def create_property(
        self,
        telegram_uid: int,
        data: PropertyCreate,
        *,
        source: EventSource = EventSource.MINI_APP,
    ) -> PropertyOut:
        """PRP-NEW (FR-PRP-1..3). Дубль адреси блокує створення й дає сигнал модератору."""
        user = await self.require_owner_account(telegram_uid)
        city = (await self._active_city(data.address.city_code)).code
        street = clean_street(data.address.street)
        house = normalize_house_number(data.address.house_number)
        key = building_key(city, street, house)
        apartment_norm = normalize_apartment(data.apartment)
        if data.kind is PropertyKind.APARTMENT and apartment_norm is None:
            raise InvalidInput("Вкажи номер квартири", field="apartment")
        if data.kind is PropertyKind.HOUSE:
            apartment_norm = None
        rooms = validate_rooms(data.rooms)
        area = validate_area(data.area)
        floor = validate_floor(data.floor)
        entrance = clean_short(data.entrance, "entrance")
        apartment = clean_short(data.apartment, "apartment") if apartment_norm else None

        duplicate_of: uuid.UUID | None = None
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            building_id = await repo.find_building(key)
            if building_id is not None:
                existing = await repo.find_property_at(building_id, apartment_norm)
                if existing is not None:
                    if existing[1] == user.id:
                        raise PropertyAlreadyYours("Цей об'єкт уже є у твоєму списку")
                    duplicate_of = existing[0]
            if duplicate_of is None:
                if building_id is None:
                    building_id = await repo.create_building(
                        NewBuilding(
                            city_code=city,
                            street=street,
                            house_number=house,
                            normalized_key=key,
                            district=clean_short(data.address.district, "district", 80),
                            lat=data.address.lat,
                            lon=data.address.lon,
                            place_id=data.address.place_id,
                        )
                    )
                property_id = await repo.create_property(
                    NewProperty(
                        owner_user_id=user.id,
                        kind=data.kind,
                        building_id=building_id,
                        apartment=apartment,
                        apartment_norm=apartment_norm,
                        entrance=entrance,
                        floor=floor,
                        rooms=rooms,
                        area=area,
                    )
                )
                await repo.add_unit(property_id, UnitKind.ENTIRE, "", area)
                await self._analytics.record(
                    tx,
                    EventName.PROPERTY_CREATED,
                    user_id=user.id,
                    source=source,
                    props={"kind": data.kind.value},
                )
                return await self._must_get(repo, property_id)

        # Дубль чужої адреси: факт пишемо як доказ і одразу кличемо модераторів —
        # підозрілий акаунт перевіряють негайно, а не чекають накопичення сигналів.
        staff = await self._users.staff_ids()
        async with self._tx.begin() as tx:
            await self._moderation.add_risk_signal(
                tx,
                user.id,
                RiskSignal.DUPLICATE_ADDRESS,
                {"existing_property_id": str(duplicate_of)},
            )
            stamp = self._clock.now().strftime("%Y%m%d%H%M%S")
            for staff_id in staff:
                if staff_id == user.id:
                    continue
                await self._notifications.enqueue(
                    tx,
                    staff_id,
                    NotificationType.MODERATION_ALERT,
                    {
                        "reason": RiskSignal.DUPLICATE_ADDRESS.value,
                        "user_name": user.display_name,
                        "telegram_uid": user.telegram_uid,
                    },
                    dedupe_key=f"moderation_alert:{user.id}:{duplicate_of}:{staff_id}:{stamp}",
                )
        raise DuplicateAddress("Об'єкт за цією адресою вже є в Домовику")

    async def list_mine(self, telegram_uid: int) -> list[PropertyOut]:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            props = await self._repo(tx).list_by_owner(user.id)
        return [self._with_completeness(p) for p in props]

    async def update_profile(
        self, telegram_uid: int, property_id: uuid.UUID, details: PropertyDetails
    ) -> PropertyOut:
        """Анкета об'єкта (FR-PRP-8): замінюється цілком. Адреса й власність не зачіпаються."""
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            prop = await self._owned(repo, property_id, user.id)
            _check_profile(prop, details)
            data = details.model_dump(mode="json", exclude_none=True)
            data["v"] = PROFILE_VERSION
            await repo.set_profile(property_id, data)
            return await self._must_get(repo, property_id)

    async def get_mine(self, telegram_uid: int, property_id: uuid.UUID) -> PropertyOut:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            return await self._owned(self._repo(tx), property_id, user.id)

    async def update_details(
        self, telegram_uid: int, property_id: uuid.UUID, data: PropertyUpdate
    ) -> PropertyOut:
        user = await self._users.require_active(telegram_uid)
        rooms = validate_rooms(data.rooms)
        area = validate_area(data.area)
        floor = validate_floor(data.floor)
        entrance = clean_short(data.entrance, "entrance")
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._owned(repo, property_id, user.id)
            await repo.update_details(
                property_id, entrance=entrance, floor=floor, rooms=rooms, area=area
            )
            return await self._must_get(repo, property_id)

    async def delete_property(self, telegram_uid: int, property_id: uuid.UUID) -> None:
        """Видалити об'єкт разом із перевірками власності, документами й фото (FR-PRP-9).

        Перевірку, що чекає на модератора, теж знімаємо: вона зникає з черги.
        Жива оренда — HasOpenLease; скасовані оренди стираються. Якщо була завершена
        оренда, об'єкт архівується: зникає у власника, а картка оренди з адресою
        лишається учасникам. Слід — в audit_log (лише ID і статус, без адреси).
        """
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            prop = await self._owned(self._repo(tx), property_id, user.id)
        archive = await self._release(prop) if self._release is not None else False
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            docs = await repo.all_documents_of_property(property_id)
            photos = await repo.list_photos(property_id)
            if archive:
                await repo.archive_property(property_id, self._clock.now())
            else:
                await repo.delete_property(property_id)
            await self._moderation.audit(
                tx,
                user.id,
                AuditAction.PROPERTY_DELETE,
                "property",
                str(property_id),
                {
                    "ownership_status": prop.ownership_status.value,
                    "documents": len(docs),
                    "archived": archive,
                },
            )
        for key in [d.storage_key for d in docs] + [p.storage_key for p in photos]:
            await self._delete_file(key)

    async def add_room(
        self, telegram_uid: int, property_id: uuid.UUID, data: RoomIn
    ) -> PropertyOut:
        """PRP-UNITS (FR-PRP-3). Заборону здавати одночасно весь об'єкт і кімнату
        перевіряють оголошення й оренди (M3)."""
        user = await self._users.require_active(telegram_uid)
        name = clean_room_name(data.name)
        area = validate_area(data.area)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            prop = await self._owned(repo, property_id, user.id)
            if await repo.count_rooms(property_id) >= max(prop.rooms, 1):
                raise InvalidInput("Кімнат більше, ніж у об'єкті", field="rooms")
            await repo.add_unit(property_id, UnitKind.ROOM, name, area)
            return await self._must_get(repo, property_id)

    async def remove_room(
        self, telegram_uid: int, property_id: uuid.UUID, unit_id: uuid.UUID
    ) -> PropertyOut:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._owned(repo, property_id, user.id)
            if not await repo.delete_room(property_id, unit_id):
                raise NotFound("Кімнату не знайдено")
            return await self._must_get(repo, property_id)

    # ---------------------------------------------- для інших модулів (правило 5)

    async def property_by_id(self, property_id: uuid.UUID) -> PropertyOut | None:
        """Для інших модулів (лічильники): права перевіряє модуль, що викликає."""
        async with self._tx.begin() as tx:
            return await self._repo(tx).get_property(property_id)

    async def property_of_unit(self, unit_id: uuid.UUID) -> PropertyOut | None:
        """Для оренд: адреса й юніти об'єкта, до якого належить юніт."""
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            property_id = await repo.property_id_of_unit(unit_id)
            if property_id is None:
                return None
            # Архівований об'єкт теж: картка завершеної оренди показує адресу.
            return await repo.get_property(property_id, include_archived=True)

    async def verified_owner_name(self, property_id: uuid.UUID) -> str | None:
        """ПІБ власника з довідки (owner_name_in_doc) — для звірки з підписом договору.

        None — власність не підтверджено модератором (наприклад, dev-обхід).
        """
        async with self._tx.begin() as tx:
            v = await self._repo(tx).latest_verification(property_id)
        if v is None or v.status is not VerificationStatus.VERIFIED:
            return None
        return v.owner_name_in_doc

    def set_release_hook(self, hook: Callable[[PropertyOut], Awaitable[bool]]) -> None:
        """Модуль оренд звільняє об'єкт перед видаленням (без циклічних імпортів).

        Кидає HasOpenLease, якщо оренда жива; True — треба архівувати, а не стирати.
        """
        self._release = hook

    async def require_verified_owner(
        self, user_id: uuid.UUID, property_id: uuid.UUID
    ) -> PropertyOut:
        """Перед публікацією оголошення й створенням оренди (FR-PRP-4)."""
        async with self._tx.begin() as tx:
            prop = await self._repo(tx).get_property(property_id)
        if prop is None or prop.owner_user_id != user_id:
            raise NotFound("Об'єкт не знайдено")
        if prop.ownership_status is not OwnershipStatus.VERIFIED:
            raise OwnershipNotVerified("Спершу підтверди власність")
        return prop

    async def require_listable(self, user_id: uuid.UUID, property_id: uuid.UUID) -> PropertyOut:
        """Перед публікацією оголошення (M6): власність підтверджена й анкета ≥ мінімуму."""
        prop = self._with_completeness(await self.require_verified_owner(user_id, property_id))
        if prop.completeness is None or not prop.completeness.publishable:
            raise ProfileIncomplete(
                "Анкета заповнена замало для оголошення",
                percent=prop.completeness.percent if prop.completeness else 0,
                required=self._config.listing_min_completeness,
            )
        return prop

    # ------------------------------------------------------- власність: власник

    async def ownership(self, telegram_uid: int, property_id: uuid.UUID) -> OwnershipOut:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            prop = await self._owned(repo, property_id, user.id)
            return await self._ownership_out(repo, prop)

    async def upload_document(
        self,
        telegram_uid: int,
        property_id: uuid.UUID,
        filename: str,
        data: bytes,
    ) -> OwnershipOut:
        """OWN-2: PDF до 10 МБ, кілька файлів (довідка, довіреність, згода)."""
        user = await self._users.require_active(telegram_uid)
        if not data.startswith(PDF_MAGIC):
            raise InvalidInput("Потрібен PDF-файл", field="file")
        if len(data) > self._config.max_document_bytes:
            raise InvalidInput("Файл завеликий", field="file")
        name = _safe_filename(filename)
        doc_id = uuid.uuid4()

        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            prop = await self._owned(repo, property_id, user.id)
            if not can_collect_documents(prop.ownership_status):
                raise Conflict("Документи зараз не приймаються", field="ownership")
            current = await repo.open_verification(property_id)
            if current is None:
                verification_id = await repo.create_verification(
                    user.id, property_id, VerificationStatus.DRAFT
                )
                count = 0
            elif current.status is VerificationStatus.DRAFT:
                verification_id = current.id
                count = len(current.documents)
            else:
                raise Conflict("Документи вже на перевірці", field="ownership")
            if count >= self._config.max_documents:
                raise TooManyDocuments("Забагато файлів", field="file")
            key = f"ownership/{verification_id}/{doc_id}.pdf"
            await self._storage.put(key, data)
            try:
                await repo.add_document(doc_id, verification_id, key, name, len(data))
                result = await self._ownership_out(repo, prop)
            except Exception:
                await self._delete_file(key)
                raise
        return result

    async def remove_document(
        self, telegram_uid: int, property_id: uuid.UUID, document_id: uuid.UUID
    ) -> OwnershipOut:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            prop = await self._owned(repo, property_id, user.id)
            current = await repo.open_verification(property_id)
            doc = await repo.get_document(document_id)
            if current is None or doc is None or doc.verification_id != current.id:
                raise NotFound("Файл не знайдено")
            if current.status is not VerificationStatus.DRAFT:
                raise Conflict("Документи вже на перевірці", field="ownership")
            await repo.delete_document(document_id)
            result = await self._ownership_out(repo, prop)
        await self._delete_file(doc.storage_key)
        return result

    async def submit_ownership(
        self,
        telegram_uid: int,
        property_id: uuid.UUID,
        *,
        source: EventSource = EventSource.MINI_APP,
    ) -> OwnershipOut:
        """OWN-2 «Надіслати на перевірку»: у чергу модератора (FR-PRP-4)."""
        user = await self._users.require_active(telegram_uid)
        now = self._clock.now()
        staff = await self._users.staff_ids()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            prop = await self._owned(repo, property_id, user.id)
            current = await repo.open_verification(property_id)
            if current is None or current.status is not VerificationStatus.DRAFT:
                raise Conflict("Немає документів для перевірки", field="ownership")
            if not current.documents:
                raise InvalidInput("Додай хоча б один документ", field="file")
            VERIFICATION.transition(current.status, VerificationStatus.PENDING)
            OWNERSHIP.transition(prop.ownership_status, OwnershipStatus.PENDING)
            await repo.update_verification(
                current.id, status=VerificationStatus.PENDING, submitted_at=now
            )
            await repo.set_ownership(property_id, OwnershipStatus.PENDING, None)
            await self._analytics.record(
                tx, EventName.OWNERSHIP_SUBMITTED, user_id=user.id, source=source
            )
            # Модераторам — не частіше разу на годину (BOT_FLOWS §18, moderation_queue).
            hour = now.strftime("%Y%m%d%H")
            for staff_id in staff:
                await self._notifications.enqueue(
                    tx,
                    staff_id,
                    NotificationType.MODERATION_QUEUE,
                    {},
                    dedupe_key=f"moderation_queue:{staff_id}:{hour}",
                )
            return await self._ownership_out(repo, await self._must_get(repo, property_id))

    async def dev_verify(self, telegram_uid: int, property_id: uuid.UUID) -> OwnershipOut:
        """FR-PRP-5: `DEV_SKIP_OWNERSHIP_CHECK` схвалює одразу (is_dev_bypass = true)."""
        if not self._config.dev_skip_ownership_check:
            raise PermissionDenied("Dev-обхід вимкнено")
        user = await self._users.require_active(telegram_uid)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            prop = await self._owned(repo, property_id, user.id)
            OWNERSHIP.transition(prop.ownership_status, OwnershipStatus.VERIFIED)
            current = await repo.open_verification(property_id)
            if current is not None:  # відкриту перевірку закриваємо dev-рішенням
                await repo.update_verification(
                    current.id,
                    status=VerificationStatus.VERIFIED,
                    submitted_at=current.submitted_at or now,
                    decided_at=now,
                    is_dev_bypass=True,
                )
            else:
                await repo.create_verification(
                    user.id,
                    property_id,
                    VerificationStatus.VERIFIED,
                    is_dev_bypass=True,
                    submitted_at=now,
                    decided_at=now,
                )
            await repo.set_ownership(property_id, OwnershipStatus.VERIFIED, now)
            await self._moderation.audit(
                tx, user.id, AuditAction.OWNERSHIP_DEV_BYPASS, "property", str(property_id)
            )
            await self._analytics.record(
                tx,
                EventName.OWNERSHIP_VERIFIED,
                user_id=user.id,
                source=EventSource.MINI_APP,
                props={"dev_bypass": True},
            )
            return await self._ownership_out(repo, await self._must_get(repo, property_id))

    # ---------------------------------------------------- власність: модератор

    async def pending_count(self, telegram_uid: int) -> int:
        await self._users.require_staff(telegram_uid)
        async with self._tx.begin() as tx:
            return await self._repo(tx).count_pending()

    async def verification_queue(self, telegram_uid: int) -> list[QueueItem]:
        """MOD-OWN: черга від найстаріших."""
        await self._users.require_staff(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            rows = await repo.list_pending(self._config.queue_limit)
            items: list[QueueItem] = []
            for row in rows:
                prop = await repo.get_property(row.property_id)
                if prop is None:
                    continue
                items.append(
                    QueueItem(
                        verification_id=row.verification_id,
                        property=self._with_completeness(prop),
                        owner=await self._owner_summary(tx, repo, row.user_id),
                        submitted_at=row.submitted_at,
                        documents_count=row.documents_count,
                    )
                )
            return items

    async def verification_case(
        self, telegram_uid: int, verification_id: uuid.UUID
    ) -> VerificationCase:
        await self._users.require_staff(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            v = await self._verification(repo, verification_id)
            prop = await self._must_get(repo, v.property_id)
            return VerificationCase(
                verification=v,
                property=prop,
                owner=await self._owner_summary(tx, repo, v.user_id),
            )

    async def send_documents_to_chat(self, telegram_uid: int, verification_id: uuid.UUID) -> int:
        """Модератор отримує файли в чат із ботом: PDF у Mini App на телефонах не відкрити."""
        await self._users.require_staff(telegram_uid)
        if self._notifier is None:
            raise ServiceUnavailable("Надсилання файлів не налаштовано")
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            v = await self._verification(repo, verification_id)
            docs = [d for d in await repo.list_documents(v.id) if d.deleted_at is None]
        sent = 0
        for doc in docs:
            data = await self._storage.get(doc.storage_key)
            try:
                await self._notifier.send_document(telegram_uid, doc.filename, data)
            except NotifierError as exc:
                raise ServiceUnavailable("Не вдалося надіслати файл у чат") from exc
            sent += 1
        return sent

    async def approve(
        self, telegram_uid: int, verification_id: uuid.UUID, owner_name_in_doc: str
    ) -> VerificationCase:
        """MOD-OWN ✅: ПІБ з довідки — для звірки з підписом договору (FR-PRP-4, п. 3, 5)."""
        staff = await self._users.require_staff(telegram_uid)
        owner_name = clean_owner_name(owner_name_in_doc)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            v = await self._verification(repo, verification_id)
            self._check_reviewer(staff, v)
            prop = await self._must_get(repo, v.property_id)
            VERIFICATION.transition(v.status, VerificationStatus.VERIFIED)
            OWNERSHIP.transition(prop.ownership_status, OwnershipStatus.VERIFIED)
            await repo.update_verification(
                v.id,
                status=VerificationStatus.VERIFIED,
                decided_at=now,
                reviewed_by=staff.id,
                owner_name_in_doc=owner_name,
            )
            await repo.set_ownership(prop.id, OwnershipStatus.VERIFIED, now)
            await self._moderation.audit(
                tx,
                staff.id,
                AuditAction.OWNERSHIP_APPROVE,
                "property",
                str(prop.id),
                {"verification_id": str(v.id)},
            )
            await self._analytics.record(
                tx, EventName.OWNERSHIP_VERIFIED, user_id=v.user_id, source=EventSource.MINI_APP
            )
            await self._notifications.enqueue(
                tx,
                v.user_id,
                NotificationType.OWNERSHIP_VERIFIED,
                _address_payload(prop),
                dedupe_key=f"ownership_verified:{v.id}",
            )
            return await self._case(tx, repo, v.id)

    async def reject(
        self, telegram_uid: int, verification_id: uuid.UUID, reason: str
    ) -> VerificationCase:
        """MOD-OWN ❌: причина йде власнику, він може надіслати документи знову."""
        staff = await self._users.require_staff(telegram_uid)
        reason = clean_reason(reason)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            v = await self._verification(repo, verification_id)
            self._check_reviewer(staff, v)
            prop = await self._must_get(repo, v.property_id)
            VERIFICATION.transition(v.status, VerificationStatus.REJECTED)
            OWNERSHIP.transition(prop.ownership_status, OwnershipStatus.REJECTED)
            await repo.update_verification(
                v.id,
                status=VerificationStatus.REJECTED,
                decided_at=now,
                reviewed_by=staff.id,
                reject_reason=reason,
            )
            await repo.set_ownership(prop.id, OwnershipStatus.REJECTED, None)
            await self._moderation.audit(
                tx,
                staff.id,
                AuditAction.OWNERSHIP_REJECT,
                "property",
                str(prop.id),
                {"verification_id": str(v.id), "reason": reason},
            )
            await self._notifications.enqueue(
                tx,
                v.user_id,
                NotificationType.OWNERSHIP_REJECTED,
                {**_address_payload(prop), "reason": reason},
                dedupe_key=f"ownership_rejected:{v.id}",
            )
            return await self._case(tx, repo, v.id)

    # ------------------------------------------------------------------ воркер

    async def purge_documents(self) -> int:
        """FR-PRP-4, п. 4: файли видаляються через N днів після рішення."""
        cutoff = self._clock.now() - timedelta(days=self._config.document_retention_days)
        async with self._tx.begin() as tx:
            docs = await self._repo(tx).documents_to_purge(cutoff)
        purged = 0
        for doc in docs:
            await self._delete_file(doc.storage_key)
            async with self._tx.begin() as tx:
                await self._repo(tx).mark_document_deleted(doc.id, self._clock.now())
            purged += 1
        return purged

    # --------------------------------------------------------------- службове

    # ------------------------------------------------------- фото (FR-PRP-10)

    async def photos(self, telegram_uid: int, property_id: uuid.UUID) -> list[PhotoOut]:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._owned(repo, property_id, user.id)
            return [_photo_out(p) for p in await repo.list_photos(property_id)]

    async def add_photo(
        self, telegram_uid: int, property_id: uuid.UUID, data: bytes
    ) -> list[PhotoOut]:
        """JPEG/PNG/WebP до 15 МБ; зберігаємо зменшений JPEG без EXIF. Нове фото — в кінець."""
        user = await self._users.require_active(telegram_uid)
        if self._images is None:
            raise ServiceUnavailable("Обробка фото не налаштована")
        if len(data) > self._config.max_photo_bytes:
            raise InvalidInput("Фото завелике", field="file", reason="too_big")
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._owned(repo, property_id, user.id)
            if len(await repo.list_photos(property_id)) >= self._config.max_photos:
                raise InvalidInput("Забагато фото", field="file", reason="too_many")
        try:
            image = await self._images.prepare(data)
        except ImageRejected as e:
            raise InvalidInput(
                "Це не фото або воно не підходить", field="file", reason=e.reason
            ) from None

        photo_id = uuid.uuid4()
        key = f"photos/{property_id}/{photo_id}.jpg"
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._owned(repo, property_id, user.id)
            current = await repo.list_photos(property_id)
            if len(current) >= self._config.max_photos:
                raise InvalidInput("Забагато фото", field="file", reason="too_many")
            await self._storage.put(key, image.data)
            try:
                photo = StoredPhoto(
                    photo_id, property_id, key, len(current), image.width, image.height
                )
                await repo.add_photo(photo, image.phash)
                result = [_photo_out(p) for p in await repo.list_photos(property_id)]
            except Exception:
                await self._delete_file(key)
                raise
        return result

    async def delete_photo(
        self, telegram_uid: int, property_id: uuid.UUID, photo_id: uuid.UUID
    ) -> list[PhotoOut]:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._owned(repo, property_id, user.id)
            photo = await self._photo_of(repo, property_id, photo_id)
            await repo.delete_photo(photo_id)
            rest = [p.id for p in await repo.list_photos(property_id) if p.id != photo_id]
            await repo.set_photo_positions(rest)
            result = [_photo_out(p) for p in await repo.list_photos(property_id)]
        await self._delete_file(photo.storage_key)
        return result

    async def make_cover(
        self, telegram_uid: int, property_id: uuid.UUID, photo_id: uuid.UUID
    ) -> list[PhotoOut]:
        """Зробити фото першим (обкладинкою); порядок інших не змінюється."""
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._owned(repo, property_id, user.id)
            await self._photo_of(repo, property_id, photo_id)
            order = [p.id for p in await repo.list_photos(property_id) if p.id != photo_id]
            await repo.set_photo_positions([photo_id, *order])
            return [_photo_out(p) for p in await repo.list_photos(property_id)]

    async def photo_file(
        self, telegram_uid: int, property_id: uuid.UUID, photo_id: uuid.UUID
    ) -> bytes:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._owned(repo, property_id, user.id)
            photo = await self._photo_of(repo, property_id, photo_id)
        return await self._storage.get(photo.storage_key)

    # Для інших модулів (оренди, далі оголошення): доступ перевіряє модуль, що викликає.

    async def photos_of(self, property_id: uuid.UUID) -> list[PhotoOut]:
        async with self._tx.begin() as tx:
            return [_photo_out(p) for p in await self._repo(tx).list_photos(property_id)]

    async def photo_data(self, property_id: uuid.UUID, photo_id: uuid.UUID) -> bytes:
        async with self._tx.begin() as tx:
            photo = await self._photo_of(self._repo(tx), property_id, photo_id)
        return await self._storage.get(photo.storage_key)

    async def _photo_of(
        self, repo: PropertiesRepository, property_id: uuid.UUID, photo_id: uuid.UUID
    ) -> StoredPhoto:
        photo = await repo.get_photo(photo_id)
        if photo is None or photo.property_id != property_id:
            raise NotFound("Фото не знайдено")
        return photo

    async def _must_get(self, repo: PropertiesRepository, property_id: uuid.UUID) -> PropertyOut:
        prop = await repo.get_property(property_id)
        if prop is None:
            raise NotFound("Об'єкт не знайдено")
        return self._with_completeness(prop)

    async def _owned(
        self, repo: PropertiesRepository, property_id: uuid.UUID, user_id: uuid.UUID
    ) -> PropertyOut:
        """Чужий об'єкт виглядає як неіснуючий — не розкриваємо, що він є."""
        prop = await repo.get_property(property_id)
        if prop is None or prop.owner_user_id != user_id:
            raise NotFound("Об'єкт не знайдено")
        return self._with_completeness(prop)

    def _with_completeness(self, prop: PropertyOut) -> PropertyOut:
        answers: dict[str, object] = prop.details.model_dump()
        answers.update(rooms=prop.rooms, area_total=prop.area, floor=prop.floor)
        c = completeness(
            answers,
            medium_from=self._config.completeness_medium_from,
            high_from=self._config.completeness_high_from,
            publish_from=self._config.listing_min_completeness,
        )
        return prop.model_copy(
            update={
                "completeness": CompletenessOut(
                    percent=c.percent,
                    level=c.level,
                    publish_min=self._config.listing_min_completeness,
                    publishable=c.publishable,
                    answered=c.answered,
                    total=c.total,
                    missing_important=list(c.missing_important),
                    sections=c.sections,
                )
            }
        )

    @staticmethod
    async def _verification(
        repo: PropertiesRepository, verification_id: uuid.UUID
    ) -> VerificationOut:
        v = await repo.get_verification(verification_id)
        if v is None:
            raise NotFound("Перевірку не знайдено")
        return v

    def _check_reviewer(self, staff: UserOut, v: VerificationOut) -> None:
        if v.user_id == staff.id and not self._config.allow_self_review:
            raise PermissionDenied("Не можна перевіряти власний об'єкт")

    async def _ownership_out(self, repo: PropertiesRepository, prop: PropertyOut) -> OwnershipOut:
        current = await repo.open_verification(prop.id) or await repo.latest_verification(prop.id)
        return OwnershipOut(
            property_id=prop.id,
            ownership_status=prop.ownership_status,
            verification=current,
            dev_bypass_available=self._config.dev_skip_ownership_check,
            max_documents=self._config.max_documents,
            max_document_mb=self._config.max_document_bytes // (1024 * 1024),
        )

    async def _owner_summary(
        self, tx: Tx, repo: PropertiesRepository, user_id: uuid.UUID
    ) -> OwnerSummary:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFound("Власника не знайдено")
        return OwnerSummary(
            user_id=user.id,
            display_name=user.display_name,
            registered_at=user.created_at,
            risk_signals=await self._moderation.count_risk_signals(tx, user.id),
            properties_count=await repo.count_by_owner(user.id),
        )

    async def _case(
        self, tx: Tx, repo: PropertiesRepository, verification_id: uuid.UUID
    ) -> VerificationCase:
        v = await self._verification(repo, verification_id)
        return VerificationCase(
            verification=v,
            property=await self._must_get(repo, v.property_id),
            owner=await self._owner_summary(tx, repo, v.user_id),
        )

    async def _delete_file(self, key: str) -> None:
        try:
            await self._storage.delete(key)
        except Exception:  # файл видалить наступне прибирання; не валимо операцію
            log.exception("Не вдалося видалити файл зі сховища")


def _check_profile(prop: PropertyOut, d: PropertyDetails) -> None:
    """Узгодженість анкети з карткою об'єкта."""
    if prop.area is not None:
        if d.area_living is not None and d.area_living > prop.area:
            raise InvalidInput("Житлова площа більша за загальну", field="area_living")
        if d.area_kitchen is not None and d.area_kitchen > prop.area:
            raise InvalidInput("Кухня більша за загальну площу", field="area_kitchen")
    if d.floors_total is not None and prop.floor is not None and prop.floor > d.floors_total:
        raise InvalidInput("Поверх вищий за поверховість будинку", field="floors_total")
    if d.description is not None:
        if not d.description.strip():
            raise InvalidInput("Порожній опис", field="description")
        if find_contacts(d.description):
            raise ContactsInText("Контакти в описі", field="description")


def _valid_house(house_number: str) -> bool:
    try:
        normalize_house_number(house_number)
    except InvalidInput:
        return False
    return True


def _safe_filename(filename: str) -> str:
    name = PurePath(filename.replace("\\", "/")).name
    name = _FILENAME_SAFE.sub("_", name).strip(" .") or "document"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    if len(name) > 100:
        name = name[:96] + ".pdf"
    return name


def _address_payload(prop: PropertyOut) -> dict[str, str | None]:
    """Що треба для тексту сповіщення. Адресу бачить лише власник — це його об'єкт."""
    return {
        "property_id": str(prop.id),
        "street": prop.street,
        "house_number": prop.house_number,
        "apartment": prop.apartment,
    }


def _photo_out(p: StoredPhoto) -> PhotoOut:
    return PhotoOut(id=p.id, position=p.position, width=p.width, height=p.height)
