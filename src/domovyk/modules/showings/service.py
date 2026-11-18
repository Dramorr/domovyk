"""Публічний інтерфейс модуля showings: слоти показів власника (FR-APP-4, ADR 0022).

Слот — час і місткість на конкретне оголошення. Хто на нього записаний — справа заявок
(`applications.slot_id`): модуль applications блокує слот через `lock(tx, …)` і сам рахує
записи. Скасування слоту повідомляє заявки через хук — записані обирають інший час.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from domovyk.core.clock import Clock
from domovyk.core.errors import Conflict, InvalidInput, NotFound, PermissionDenied
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.listings import ListingStatus
from domovyk.domain.showings import SlotRules, SlotStatus, clean_slot
from domovyk.modules.listings.service import ListingsService
from domovyk.modules.showings.repository import ShowingsRepository
from domovyk.modules.showings.schemas import SlotIn, SlotOut
from domovyk.modules.users.service import UsersService

__all__ = ["ShowingsConfig", "ShowingsService", "SlotIn", "SlotOut"]

# Слоти можна додавати на опубліковане оголошення і на паузі (погоджені заявки ще живі).
_SLOT_LISTING = frozenset({ListingStatus.ACTIVE, ListingStatus.PAUSED})


@dataclass(frozen=True)
class ShowingsConfig:
    max_open_slots: int = 30  # майбутніх відкритих слотів на оголошення
    rules: SlotRules = field(default_factory=SlotRules)


class ShowingsService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], ShowingsRepository],
        users: UsersService,
        listings: ListingsService,
        clock: Clock,
        config: ShowingsConfig,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._listings = listings
        self._clock = clock
        self._config = config
        self._cancelled_hook: Callable[[uuid.UUID], Awaitable[None]] | None = None

    def set_cancelled_hook(self, hook: Callable[[uuid.UUID], Awaitable[None]]) -> None:
        """Заявки, записані на скасований слот, повертаються до вибору часу."""
        self._cancelled_hook = hook

    # ------------------------------------------------------------- власник

    async def create(self, telegram_uid: int, property_id: uuid.UUID, data: SlotIn) -> SlotOut:
        """Новий слот на живе оголошення об'єкта власника."""
        user = await self._users.require_active(telegram_uid)
        state = await self._listings.state(telegram_uid, property_id)  # чужий об'єкт — NotFound
        listing = state.listing
        if listing is None or listing.status not in _SLOT_LISTING:
            raise Conflict("Покази — лише для опублікованого оголошення", reason="no_listing")
        now = self._clock.now()
        starts, ends, capacity = clean_slot(
            data.day, data.start, data.minutes, data.capacity, now, self._config.rules
        )
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            if await repo.count_open(listing.id, now) >= self._config.max_open_slots:
                raise Conflict("Забагато слотів", reason="too_many")
            for other in await repo.by_listing(listing.id, now):
                if (
                    other.status is SlotStatus.OPEN
                    and other.starts_at < ends
                    and starts < other.ends_at
                ):
                    raise InvalidInput(
                        "Цей час перетинається з іншим показом", field="start", reason="overlap"
                    )
            return await repo.create(listing.id, user.id, starts, ends, capacity)

    async def cancel(self, telegram_uid: int, slot_id: uuid.UUID) -> SlotOut:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            slot = await repo.lock(slot_id)
            if slot is None or slot.host_user_id != user.id:
                raise NotFound("Показ не знайдено")
            if slot.status is not SlotStatus.OPEN:
                raise Conflict("Показ уже скасовано", reason="not_open")
            if slot.ends_at <= self._clock.now():
                raise PermissionDenied("Показ уже минув", reason="past")
            await repo.set_status(slot_id, SlotStatus.CANCELLED)
        if self._cancelled_hook is not None:
            await self._cancelled_hook(slot_id)
        return slot.model_copy(update={"status": SlotStatus.CANCELLED})

    # ------------------------------------------------- для модуля applications

    async def upcoming(self, listing_id: uuid.UUID) -> list[SlotOut]:
        """Слоти оголошення, що ще не закінчились (і скасовані — для історії записаних)."""
        async with self._tx.begin() as tx:
            return await self._repo(tx).by_listing(listing_id, self._clock.now())

    async def by_ids(self, slot_ids: list[uuid.UUID]) -> dict[uuid.UUID, SlotOut]:
        async with self._tx.begin() as tx:
            return {s.id: s for s in await self._repo(tx).by_ids(slot_ids)}

    async def lock(self, tx: Tx, slot_id: uuid.UUID) -> SlotOut | None:
        """У транзакції заявок: слот блокується до кінця запису на нього."""
        return await self._repo(tx).lock(slot_id)
