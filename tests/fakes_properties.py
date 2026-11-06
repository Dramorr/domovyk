"""Фейки для модулів properties і notifications та адаптерів (сховище, Telegram).

Лише вигадані дані.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from domovyk.adapters.notifier.base import Button, NotifierError
from domovyk.adapters.storage.base import FileNotFoundInStorage
from domovyk.domain.properties import OwnershipStatus, PropertyKind, UnitKind, VerificationStatus
from domovyk.modules.notifications.repository import GIVE_UP_ATTEMPTS
from domovyk.modules.notifications.schemas import PendingNotification
from domovyk.modules.properties.repository import (
    NewBuilding,
    NewProperty,
    PendingRow,
    StoredDocument,
)
from domovyk.modules.properties.schemas import (
    DocumentOut,
    PropertyDetails,
    PropertyOut,
    UnitOut,
    VerificationOut,
)

_T0 = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


@dataclass
class _Building:
    id: uuid.UUID
    data: NewBuilding


@dataclass
class _Property:
    id: uuid.UUID
    data: NewProperty
    entrance: str | None
    floor: int | None
    rooms: int
    area: Decimal | None
    ownership: OwnershipStatus = OwnershipStatus.UNVERIFIED
    verified_at: datetime | None = None
    created_at: datetime = _T0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Unit:
    id: uuid.UUID
    property_id: uuid.UUID
    kind: UnitKind
    name: str
    area: Decimal | None


@dataclass
class _Verification:
    id: uuid.UUID
    user_id: uuid.UUID
    property_id: uuid.UUID
    status: VerificationStatus
    is_dev_bypass: bool = False
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    reviewed_by: uuid.UUID | None = None
    reject_reason: str | None = None
    owner_name_in_doc: str | None = None
    created_at: datetime = _T0
    seq: int = 0


@dataclass
class _Notification:
    id: int
    user_id: uuid.UUID
    type: str
    payload: dict[str, Any]
    dedupe_key: str
    send_after: datetime
    sent_at: datetime | None = None
    attempts: int = 0
    last_error: str | None = None


@dataclass
class PropDb:
    buildings: dict[str, _Building] = field(default_factory=dict)
    properties: dict[uuid.UUID, _Property] = field(default_factory=dict)
    units: dict[uuid.UUID, _Unit] = field(default_factory=dict)
    verifications: dict[uuid.UUID, _Verification] = field(default_factory=dict)
    documents: dict[uuid.UUID, StoredDocument] = field(default_factory=dict)
    notifications: list[_Notification] = field(default_factory=list)
    prefs: dict[tuple[uuid.UUID, uuid.UUID, str], tuple[bool, dict[str, Any]]] = field(
        default_factory=dict
    )


class FakePropertiesRepository:
    def __init__(self, db: PropDb) -> None:
        self.db = db

    async def find_building(self, normalized_key: str) -> uuid.UUID | None:
        b = self.db.buildings.get(normalized_key)
        return b.id if b else None

    async def create_building(self, building: NewBuilding) -> uuid.UUID:
        b = _Building(uuid.uuid4(), building)
        self.db.buildings[building.normalized_key] = b
        return b.id

    def _building(self, building_id: uuid.UUID) -> _Building:
        return next(b for b in self.db.buildings.values() if b.id == building_id)

    async def find_property_at(
        self, building_id: uuid.UUID, apartment_norm: str | None
    ) -> tuple[uuid.UUID, uuid.UUID] | None:
        for p in self.db.properties.values():
            if p.data.building_id == building_id and p.data.apartment_norm == apartment_norm:
                return p.id, p.data.owner_user_id
        return None

    async def create_property(self, data: NewProperty) -> uuid.UUID:
        p = _Property(uuid.uuid4(), data, data.entrance, data.floor, data.rooms, data.area)
        self.db.properties[p.id] = p
        return p.id

    def _out(self, p: _Property) -> PropertyOut:
        b = self._building(p.data.building_id).data
        units = [
            UnitOut(id=u.id, kind=u.kind, name=u.name, area=u.area, is_active=True)
            for u in sorted(self.db.units.values(), key=lambda u: u.kind != UnitKind.ENTIRE)
            if u.property_id == p.id
        ]
        return PropertyOut(
            id=p.id,
            owner_user_id=p.data.owner_user_id,
            kind=p.data.kind,
            city=b.city,
            street=b.street,
            house_number=b.house_number,
            district=b.district,
            apartment=p.data.apartment,
            entrance=p.entrance,
            floor=p.floor,
            rooms=p.rooms,
            area=p.area,
            has_location=b.lat is not None,
            ownership_status=p.ownership,
            ownership_verified_at=p.verified_at,
            units=units,
            created_at=p.created_at,
            details=PropertyDetails.model_validate(
                {k: v for k, v in p.details.items() if k != "v"}
            ),
        )

    async def get_property(self, property_id: uuid.UUID) -> PropertyOut | None:
        p = self.db.properties.get(property_id)
        return self._out(p) if p else None

    async def list_by_owner(self, owner_user_id: uuid.UUID) -> list[PropertyOut]:
        return [
            self._out(p)
            for p in self.db.properties.values()
            if p.data.owner_user_id == owner_user_id
        ]

    async def count_by_owner(self, owner_user_id: uuid.UUID) -> int:
        return len(await self.list_by_owner(owner_user_id))

    async def update_details(
        self,
        property_id: uuid.UUID,
        *,
        entrance: str | None,
        floor: int | None,
        rooms: int,
        area: Decimal | None,
    ) -> None:
        p = self.db.properties[property_id]
        p.entrance, p.floor, p.rooms, p.area = entrance, floor, rooms, area

    async def delete_property(self, property_id: uuid.UUID) -> None:
        self.db.properties.pop(property_id)
        for uid in [u.id for u in self.db.units.values() if u.property_id == property_id]:
            del self.db.units[uid]
        for v in [v for v in self.db.verifications.values() if v.property_id == property_id]:
            del self.db.verifications[v.id]
            for d in [d for d in self.db.documents.values() if d.verification_id == v.id]:
                del self.db.documents[d.id]

    async def property_id_of_unit(self, unit_id: uuid.UUID) -> uuid.UUID | None:
        u = self.db.units.get(unit_id)
        return u.property_id if u else None

    async def set_profile(self, property_id: uuid.UUID, details: dict[str, Any]) -> None:
        self.db.properties[property_id].details = dict(details)

    async def set_ownership(
        self, property_id: uuid.UUID, status: OwnershipStatus, verified_at: datetime | None
    ) -> None:
        p = self.db.properties[property_id]
        p.ownership, p.verified_at = status, verified_at

    async def add_unit(
        self, property_id: uuid.UUID, kind: UnitKind, name: str, area: Decimal | None
    ) -> uuid.UUID:
        if kind is UnitKind.ENTIRE and any(
            u.property_id == property_id and u.kind is UnitKind.ENTIRE
            for u in self.db.units.values()
        ):
            raise AssertionError("uq_one_entire_unit порушено")
        u = _Unit(uuid.uuid4(), property_id, kind, name, area)
        self.db.units[u.id] = u
        return u.id

    async def delete_room(self, property_id: uuid.UUID, unit_id: uuid.UUID) -> bool:
        u = self.db.units.get(unit_id)
        if u is None or u.property_id != property_id or u.kind is not UnitKind.ROOM:
            return False
        del self.db.units[unit_id]
        return True

    async def count_rooms(self, property_id: uuid.UUID) -> int:
        return sum(
            1
            for u in self.db.units.values()
            if u.property_id == property_id and u.kind is UnitKind.ROOM
        )

    def _v_out(self, v: _Verification) -> VerificationOut:
        docs = [d for d in self.db.documents.values() if d.verification_id == v.id]
        return VerificationOut(
            id=v.id,
            property_id=v.property_id,
            user_id=v.user_id,
            status=v.status,
            is_dev_bypass=v.is_dev_bypass,
            reject_reason=v.reject_reason,
            owner_name_in_doc=v.owner_name_in_doc,
            created_at=v.created_at,
            submitted_at=v.submitted_at,
            decided_at=v.decided_at,
            documents=[
                DocumentOut(
                    id=d.id,
                    filename=d.filename,
                    size_bytes=d.size_bytes,
                    uploaded_at=d.uploaded_at,
                    deleted=d.deleted_at is not None,
                )
                for d in docs
            ],
        )

    async def open_verification(self, property_id: uuid.UUID) -> VerificationOut | None:
        for v in self.db.verifications.values():
            if v.property_id == property_id and v.status in (
                VerificationStatus.DRAFT,
                VerificationStatus.PENDING,
            ):
                return self._v_out(v)
        return None

    async def latest_verification(self, property_id: uuid.UUID) -> VerificationOut | None:
        mine = [v for v in self.db.verifications.values() if v.property_id == property_id]
        return self._v_out(max(mine, key=lambda v: v.seq)) if mine else None

    async def get_verification(self, verification_id: uuid.UUID) -> VerificationOut | None:
        v = self.db.verifications.get(verification_id)
        return self._v_out(v) if v else None

    async def create_verification(
        self,
        user_id: uuid.UUID,
        property_id: uuid.UUID,
        status: VerificationStatus,
        *,
        is_dev_bypass: bool = False,
        submitted_at: datetime | None = None,
        decided_at: datetime | None = None,
    ) -> uuid.UUID:
        if status in (VerificationStatus.DRAFT, VerificationStatus.PENDING) and (
            await self.open_verification(property_id)
        ):
            raise AssertionError("uq_open_ownership_verification порушено")
        v = _Verification(
            uuid.uuid4(),
            user_id,
            property_id,
            status,
            is_dev_bypass=is_dev_bypass,
            submitted_at=submitted_at,
            decided_at=decided_at,
            seq=len(self.db.verifications),
        )
        self.db.verifications[v.id] = v
        return v.id

    async def update_verification(self, verification_id: uuid.UUID, **values: Any) -> None:
        v = self.db.verifications[verification_id]
        for k, val in values.items():
            setattr(v, k, val)

    async def list_pending(self, limit: int) -> list[PendingRow]:
        rows = [
            PendingRow(
                v.id,
                v.property_id,
                v.user_id,
                v.submitted_at,
                sum(1 for d in self.db.documents.values() if d.verification_id == v.id),
            )
            for v in self.db.verifications.values()
            if v.status is VerificationStatus.PENDING
        ]
        return sorted(rows, key=lambda r: r.submitted_at or _T0)[:limit]

    async def count_pending(self) -> int:
        return len(await self.list_pending(10_000))

    async def add_document(
        self,
        document_id: uuid.UUID,
        verification_id: uuid.UUID,
        storage_key: str,
        filename: str,
        size_bytes: int,
    ) -> None:
        self.db.documents[document_id] = StoredDocument(
            document_id, verification_id, storage_key, filename, size_bytes, _T0, None
        )

    async def get_document(self, document_id: uuid.UUID) -> StoredDocument | None:
        return self.db.documents.get(document_id)

    async def list_documents(self, verification_id: uuid.UUID) -> list[StoredDocument]:
        return [d for d in self.db.documents.values() if d.verification_id == verification_id]

    async def delete_document(self, document_id: uuid.UUID) -> None:
        self.db.documents.pop(document_id, None)

    async def mark_document_deleted(self, document_id: uuid.UUID, at: datetime) -> None:
        d = self.db.documents[document_id]
        self.db.documents[document_id] = StoredDocument(
            d.id, d.verification_id, d.storage_key, d.filename, d.size_bytes, d.uploaded_at, at
        )

    async def documents_to_purge(self, decided_before: datetime) -> list[StoredDocument]:
        out = []
        for d in self.db.documents.values():
            v = self.db.verifications[d.verification_id]
            if d.deleted_at is None and v.decided_at is not None and v.decided_at < decided_before:
                out.append(d)
        return out

    async def all_documents_of_property(self, property_id: uuid.UUID) -> list[StoredDocument]:
        ids = {v.id for v in self.db.verifications.values() if v.property_id == property_id}
        return [d for d in self.db.documents.values() if d.verification_id in ids]


class FakeNotificationsRepository:
    def __init__(self, db: PropDb) -> None:
        self.db = db

    async def add(
        self,
        user_id: uuid.UUID,
        type_: str,
        payload: dict[str, Any],
        dedupe_key: str,
        send_after: datetime,
    ) -> bool:
        if any(n.dedupe_key == dedupe_key for n in self.db.notifications):
            return False
        self.db.notifications.append(
            _Notification(
                len(self.db.notifications) + 1, user_id, type_, payload, dedupe_key, send_after
            )
        )
        return True

    async def claim_next(self, now: datetime, max_attempts: int) -> PendingNotification | None:
        for n in self.db.notifications:
            if n.sent_at is None and n.send_after <= now and n.attempts < max_attempts:
                return PendingNotification(n.id, n.user_id, n.type, dict(n.payload))
        return None

    def _get(self, notification_id: int) -> _Notification:
        return next(n for n in self.db.notifications if n.id == notification_id)

    async def mark_sent(self, notification_id: int, at: datetime) -> None:
        n = self._get(notification_id)
        n.sent_at, n.attempts = at, n.attempts + 1

    async def prefs(
        self, user_id: uuid.UUID, lease_id: uuid.UUID
    ) -> dict[str, tuple[bool, dict[str, Any]]]:
        return {
            t: v for (u, lid, t), v in self.db.prefs.items() if u == user_id and lid == lease_id
        }

    async def set_pref(
        self,
        user_id: uuid.UUID,
        lease_id: uuid.UUID,
        type_: str,
        enabled: bool,
        params: dict[str, Any],
    ) -> None:
        self.db.prefs[(user_id, lease_id, type_)] = (enabled, dict(params))

    async def mark_failed(
        self, notification_id: int, error: str, *, retry_at: datetime | None
    ) -> None:
        n = self._get(notification_id)
        n.last_error = error
        if retry_at is None:
            n.attempts = GIVE_UP_ATTEMPTS
        else:
            n.attempts += 1
            n.send_after = retry_at


class FakePrefs:
    """Домішка до FakeNotificationsRepository: налаштування сповіщень."""


class MemoryStorage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.files[key] = data

    async def get(self, key: str) -> bytes:
        if key not in self.files:
            raise FileNotFoundInStorage(key)
        return self.files[key]

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)


@dataclass
class SentMessage:
    chat_id: int
    text: str
    buttons: list[Button]
    silent: bool = False


class FakeNotifier:
    def __init__(self, fail: NotifierError | None = None) -> None:
        self.messages: list[SentMessage] = []
        self.documents: list[tuple[int, str, bytes]] = []
        self.fail = fail

    async def send_message(
        self, chat_id: int, text: str, buttons: Sequence[Button] = (), *, silent: bool = False
    ) -> None:
        if self.fail:
            raise self.fail
        self.messages.append(SentMessage(chat_id, text, list(buttons), silent))

    async def send_document(
        self, chat_id: int, filename: str, data: bytes, caption: str | None = None
    ) -> None:
        if self.fail:
            raise self.fail
        self.documents.append((chat_id, filename, data))


PDF = b"%PDF-1.4\n% fake test document\n%%EOF\n"
__all__ = [
    "PDF",
    "FakeNotificationsRepository",
    "FakeNotifier",
    "FakePropertiesRepository",
    "MemoryStorage",
    "PropDb",
    "PropertyKind",
]
