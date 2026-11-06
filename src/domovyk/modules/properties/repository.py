"""Доступ до districts, buildings, properties, units, user_verifications (ownership),
verification_documents. Лише всередині модуля properties."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Row, delete, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert

from domovyk.core.tx import Tx, session_of
from domovyk.domain.properties import OwnershipStatus, PropertyKind, UnitKind, VerificationStatus
from domovyk.modules.properties.models import (
    BuildingRow,
    DistrictRow,
    PropertyRow,
    UnitRow,
    VerificationDocumentRow,
    VerificationRow,
)
from domovyk.modules.properties.schemas import (
    DocumentOut,
    PropertyDetails,
    PropertyOut,
    UnitOut,
    VerificationOut,
)

OWNERSHIP_KIND = "ownership"


@dataclass(frozen=True)
class NewBuilding:
    city: str
    street: str
    house_number: str
    normalized_key: str
    district: str | None
    lat: float | None
    lon: float | None
    place_id: str | None


@dataclass(frozen=True)
class NewProperty:
    owner_user_id: uuid.UUID
    kind: PropertyKind
    building_id: uuid.UUID
    apartment: str | None
    apartment_norm: str | None
    entrance: str | None
    floor: int | None
    rooms: int
    area: Decimal | None


@dataclass(frozen=True)
class StoredDocument:
    id: uuid.UUID
    verification_id: uuid.UUID
    storage_key: str
    filename: str
    size_bytes: int
    uploaded_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True)
class PendingRow:
    verification_id: uuid.UUID
    property_id: uuid.UUID
    user_id: uuid.UUID
    submitted_at: datetime | None
    documents_count: int


class PropertiesRepository(Protocol):
    async def find_building(self, normalized_key: str) -> uuid.UUID | None: ...
    async def create_building(self, building: NewBuilding) -> uuid.UUID: ...
    async def find_property_at(
        self, building_id: uuid.UUID, apartment_norm: str | None
    ) -> tuple[uuid.UUID, uuid.UUID] | None: ...

    async def create_property(self, data: NewProperty) -> uuid.UUID: ...
    async def get_property(self, property_id: uuid.UUID) -> PropertyOut | None: ...
    async def list_by_owner(self, owner_user_id: uuid.UUID) -> list[PropertyOut]: ...
    async def count_by_owner(self, owner_user_id: uuid.UUID) -> int: ...
    async def update_details(
        self,
        property_id: uuid.UUID,
        *,
        entrance: str | None,
        floor: int | None,
        rooms: int,
        area: Decimal | None,
    ) -> None: ...
    async def delete_property(self, property_id: uuid.UUID) -> None: ...
    async def property_id_of_unit(self, unit_id: uuid.UUID) -> uuid.UUID | None: ...
    async def set_profile(self, property_id: uuid.UUID, details: dict[str, Any]) -> None: ...
    async def set_ownership(
        self, property_id: uuid.UUID, status: OwnershipStatus, verified_at: datetime | None
    ) -> None: ...

    async def add_unit(
        self, property_id: uuid.UUID, kind: UnitKind, name: str, area: Decimal | None
    ) -> uuid.UUID: ...
    async def delete_room(self, property_id: uuid.UUID, unit_id: uuid.UUID) -> bool: ...
    async def count_rooms(self, property_id: uuid.UUID) -> int: ...

    async def open_verification(self, property_id: uuid.UUID) -> VerificationOut | None: ...
    async def latest_verification(self, property_id: uuid.UUID) -> VerificationOut | None: ...
    async def get_verification(self, verification_id: uuid.UUID) -> VerificationOut | None: ...
    async def create_verification(
        self,
        user_id: uuid.UUID,
        property_id: uuid.UUID,
        status: VerificationStatus,
        *,
        is_dev_bypass: bool = False,
        submitted_at: datetime | None = None,
        decided_at: datetime | None = None,
    ) -> uuid.UUID: ...
    async def update_verification(self, verification_id: uuid.UUID, **values: Any) -> None: ...
    async def list_pending(self, limit: int) -> list[PendingRow]: ...
    async def count_pending(self) -> int: ...

    async def add_document(
        self,
        document_id: uuid.UUID,
        verification_id: uuid.UUID,
        storage_key: str,
        filename: str,
        size_bytes: int,
    ) -> None: ...
    async def get_document(self, document_id: uuid.UUID) -> StoredDocument | None: ...
    async def list_documents(self, verification_id: uuid.UUID) -> list[StoredDocument]: ...
    async def delete_document(self, document_id: uuid.UUID) -> None: ...
    async def mark_document_deleted(self, document_id: uuid.UUID, at: datetime) -> None: ...
    async def documents_to_purge(self, decided_before: datetime) -> list[StoredDocument]: ...
    async def all_documents_of_property(self, property_id: uuid.UUID) -> list[StoredDocument]: ...


_PROPERTY_COLUMNS: tuple[Any, ...] = (
    PropertyRow,
    BuildingRow.city,
    BuildingRow.street,
    BuildingRow.house_number,
    DistrictRow.name.label("district"),
    literal_column("buildings.location IS NOT NULL").label("has_location"),
)


def _property_query() -> Any:
    return (
        select(*_PROPERTY_COLUMNS)
        .join(BuildingRow, BuildingRow.id == PropertyRow.building_id)
        .outerjoin(DistrictRow, DistrictRow.id == BuildingRow.district_id)
    )


def _details(raw: dict[str, Any] | None) -> PropertyDetails:
    """Анкета з БД. Невідомі (старі) ключі ігноруються, "v" — службове."""
    data = {k: v for k, v in (raw or {}).items() if k in PropertyDetails.model_fields}
    return PropertyDetails.model_validate(data)


class SqlPropertiesRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    # -------------------------------------------------------------- будинки

    async def find_building(self, normalized_key: str) -> uuid.UUID | None:
        return await self._s.scalar(
            select(BuildingRow.id).where(BuildingRow.normalized_key == normalized_key)
        )

    async def _district_id(self, city: str, name: str) -> int:
        stmt = (
            insert(DistrictRow)
            .values(city=city, name=name)
            .on_conflict_do_nothing(index_elements=[DistrictRow.city, DistrictRow.name])
            .returning(DistrictRow.id)
        )
        district_id = await self._s.scalar(stmt)
        if district_id is None:
            district_id = await self._s.scalar(
                select(DistrictRow.id).where(DistrictRow.city == city, DistrictRow.name == name)
            )
        if district_id is None:
            raise RuntimeError("Район не знайдено після вставки")
        return int(district_id)

    async def create_building(self, building: NewBuilding) -> uuid.UUID:
        district_id = (
            await self._district_id(building.city, building.district) if building.district else None
        )
        stmt = (
            insert(BuildingRow)
            .values(
                city=building.city,
                street=building.street,
                house_number=building.house_number,
                normalized_key=building.normalized_key,
                district_id=district_id,
                geocoder_place_id=building.place_id,
            )
            .on_conflict_do_nothing(index_elements=[BuildingRow.normalized_key])
            .returning(BuildingRow.id)
        )
        building_id = await self._s.scalar(stmt)
        if building_id is None:  # паралельно створили той самий будинок
            found = await self.find_building(building.normalized_key)
            if found is None:
                raise RuntimeError("Будинок зник під час створення")
            return found
        if building.lat is not None and building.lon is not None:
            await self._s.execute(
                text(
                    "UPDATE buildings SET location = "
                    "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography WHERE id = :id"
                ),
                {"lon": building.lon, "lat": building.lat, "id": building_id},
            )
        return building_id

    # -------------------------------------------------------------- об'єкти

    async def find_property_at(
        self, building_id: uuid.UUID, apartment_norm: str | None
    ) -> tuple[uuid.UUID, uuid.UUID] | None:
        row = (
            await self._s.execute(
                select(PropertyRow.id, PropertyRow.owner_user_id).where(
                    PropertyRow.building_id == building_id,
                    func.coalesce(PropertyRow.apartment_number_norm, "") == (apartment_norm or ""),
                )
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def create_property(self, data: NewProperty) -> uuid.UUID:
        row = PropertyRow(
            owner_user_id=data.owner_user_id,
            kind=data.kind,
            building_id=data.building_id,
            apartment_number=data.apartment,
            apartment_number_norm=data.apartment_norm,
            entrance=data.entrance,
            floor=data.floor,
            rooms=data.rooms,
            area_total=data.area,
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def _units(self, property_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[UnitOut]]:
        result: dict[uuid.UUID, list[UnitOut]] = {pid: [] for pid in property_ids}
        if not property_ids:
            return result
        rows = await self._s.scalars(
            select(UnitRow)
            .where(UnitRow.property_id.in_(property_ids))
            .order_by(UnitRow.kind, UnitRow.created_at)
        )
        for u in rows:
            result[u.property_id].append(
                UnitOut(id=u.id, kind=u.kind, name=u.name, area=u.area, is_active=u.is_active)
            )
        return result

    @staticmethod
    def _to_out(row: Row[Any], units: list[UnitOut]) -> PropertyOut:
        p: PropertyRow = row[0]
        return PropertyOut(
            id=p.id,
            owner_user_id=p.owner_user_id,
            kind=p.kind,
            city=row.city,
            street=row.street,
            house_number=row.house_number,
            district=row.district,
            apartment=p.apartment_number,
            entrance=p.entrance,
            floor=p.floor,
            rooms=p.rooms,
            area=p.area_total,
            has_location=bool(row.has_location),
            ownership_status=p.ownership_status,
            ownership_verified_at=p.ownership_verified_at,
            units=units,
            created_at=p.created_at,
            details=_details(p.details),
        )

    async def get_property(self, property_id: uuid.UUID) -> PropertyOut | None:
        row = (
            await self._s.execute(
                _property_query()
                .where(PropertyRow.id == property_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        if row is None:
            return None
        units = await self._units([property_id])
        return self._to_out(row, units[property_id])

    async def list_by_owner(self, owner_user_id: uuid.UUID) -> list[PropertyOut]:
        rows = (
            await self._s.execute(
                _property_query()
                .where(PropertyRow.owner_user_id == owner_user_id)
                .order_by(PropertyRow.created_at)
                .execution_options(populate_existing=True)
            )
        ).all()
        units = await self._units([r[0].id for r in rows])
        return [self._to_out(r, units[r[0].id]) for r in rows]

    async def count_by_owner(self, owner_user_id: uuid.UUID) -> int:
        count = await self._s.scalar(
            select(func.count())
            .select_from(PropertyRow)
            .where(PropertyRow.owner_user_id == owner_user_id)
        )
        return int(count or 0)

    async def update_details(
        self,
        property_id: uuid.UUID,
        *,
        entrance: str | None,
        floor: int | None,
        rooms: int,
        area: Decimal | None,
    ) -> None:
        await self._s.execute(
            update(PropertyRow)
            .where(PropertyRow.id == property_id)
            .values(entrance=entrance, floor=floor, rooms=rooms, area_total=area)
        )

    async def delete_property(self, property_id: uuid.UUID) -> None:
        await self._s.execute(delete(PropertyRow).where(PropertyRow.id == property_id))

    async def property_id_of_unit(self, unit_id: uuid.UUID) -> uuid.UUID | None:
        return await self._s.scalar(select(UnitRow.property_id).where(UnitRow.id == unit_id))

    async def set_profile(self, property_id: uuid.UUID, details: dict[str, Any]) -> None:
        await self._s.execute(
            update(PropertyRow).where(PropertyRow.id == property_id).values(details=details)
        )

    async def set_ownership(
        self, property_id: uuid.UUID, status: OwnershipStatus, verified_at: datetime | None
    ) -> None:
        await self._s.execute(
            update(PropertyRow)
            .where(PropertyRow.id == property_id)
            .values(ownership_status=status, ownership_verified_at=verified_at)
        )

    # ---------------------------------------------------------------- юніти

    async def add_unit(
        self, property_id: uuid.UUID, kind: UnitKind, name: str, area: Decimal | None
    ) -> uuid.UUID:
        row = UnitRow(property_id=property_id, kind=kind, name=name, area=area)
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def delete_room(self, property_id: uuid.UUID, unit_id: uuid.UUID) -> bool:
        result = await self._s.execute(
            delete(UnitRow).where(
                UnitRow.id == unit_id,
                UnitRow.property_id == property_id,
                UnitRow.kind == UnitKind.ROOM,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def count_rooms(self, property_id: uuid.UUID) -> int:
        count = await self._s.scalar(
            select(func.count())
            .select_from(UnitRow)
            .where(UnitRow.property_id == property_id, UnitRow.kind == UnitKind.ROOM)
        )
        return int(count or 0)

    # ----------------------------------------------------------- перевірки

    async def _verification_out(self, row: VerificationRow) -> VerificationOut:
        if row.property_id is None:  # kind='ownership' завжди з об'єктом (CHECK у БД)
            raise RuntimeError("Перевірка власності без об'єкта")
        docs = await self.list_documents(row.id)
        return VerificationOut(
            id=row.id,
            property_id=row.property_id,
            user_id=row.user_id,
            status=row.status,
            is_dev_bypass=row.is_dev_bypass,
            reject_reason=row.reject_reason,
            owner_name_in_doc=row.owner_name_in_doc,
            created_at=row.created_at,
            submitted_at=row.submitted_at,
            decided_at=row.decided_at,
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
        row = await self._s.scalar(
            select(VerificationRow)
            .where(
                VerificationRow.property_id == property_id,
                VerificationRow.kind == OWNERSHIP_KIND,
                VerificationRow.status.in_([VerificationStatus.DRAFT, VerificationStatus.PENDING]),
            )
            .execution_options(populate_existing=True)
        )
        return await self._verification_out(row) if row else None

    async def latest_verification(self, property_id: uuid.UUID) -> VerificationOut | None:
        row = await self._s.scalar(
            select(VerificationRow)
            .where(
                VerificationRow.property_id == property_id,
                VerificationRow.kind == OWNERSHIP_KIND,
            )
            .order_by(VerificationRow.created_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return await self._verification_out(row) if row else None

    async def get_verification(self, verification_id: uuid.UUID) -> VerificationOut | None:
        row = await self._s.scalar(
            select(VerificationRow)
            .where(VerificationRow.id == verification_id, VerificationRow.kind == OWNERSHIP_KIND)
            .execution_options(populate_existing=True)
        )
        return await self._verification_out(row) if row else None

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
        row = VerificationRow(
            user_id=user_id,
            kind=OWNERSHIP_KIND,
            status=status,
            property_id=property_id,
            is_dev_bypass=is_dev_bypass,
            submitted_at=submitted_at,
            decided_at=decided_at,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return row.id

    async def update_verification(self, verification_id: uuid.UUID, **values: Any) -> None:
        await self._s.execute(
            update(VerificationRow).where(VerificationRow.id == verification_id).values(**values)
        )

    async def list_pending(self, limit: int) -> list[PendingRow]:
        docs = (
            select(func.count())
            .select_from(VerificationDocumentRow)
            .where(VerificationDocumentRow.verification_id == VerificationRow.id)
            .scalar_subquery()
        )
        rows = (
            await self._s.execute(
                select(
                    VerificationRow.id,
                    VerificationRow.property_id,
                    VerificationRow.user_id,
                    VerificationRow.submitted_at,
                    docs,
                )
                .where(
                    VerificationRow.kind == OWNERSHIP_KIND,
                    VerificationRow.status == VerificationStatus.PENDING,
                )
                .order_by(VerificationRow.submitted_at)
                .limit(limit)
            )
        ).all()
        return [PendingRow(r[0], r[1], r[2], r[3], int(r[4])) for r in rows]

    async def count_pending(self) -> int:
        count = await self._s.scalar(
            select(func.count())
            .select_from(VerificationRow)
            .where(
                VerificationRow.kind == OWNERSHIP_KIND,
                VerificationRow.status == VerificationStatus.PENDING,
            )
        )
        return int(count or 0)

    # ------------------------------------------------------------ документи

    async def add_document(
        self,
        document_id: uuid.UUID,
        verification_id: uuid.UUID,
        storage_key: str,
        filename: str,
        size_bytes: int,
    ) -> None:
        self._s.add(
            VerificationDocumentRow(
                id=document_id,
                verification_id=verification_id,
                storage_key=storage_key,
                filename=filename,
                size_bytes=size_bytes,
            )
        )
        await self._s.flush()

    @staticmethod
    def _doc(row: VerificationDocumentRow) -> StoredDocument:
        return StoredDocument(
            id=row.id,
            verification_id=row.verification_id,
            storage_key=row.storage_key,
            filename=row.filename,
            size_bytes=row.size_bytes,
            uploaded_at=row.uploaded_at,
            deleted_at=row.deleted_at,
        )

    async def get_document(self, document_id: uuid.UUID) -> StoredDocument | None:
        row = await self._s.get(VerificationDocumentRow, document_id, populate_existing=True)
        return self._doc(row) if row else None

    async def list_documents(self, verification_id: uuid.UUID) -> list[StoredDocument]:
        rows = await self._s.scalars(
            select(VerificationDocumentRow)
            .where(VerificationDocumentRow.verification_id == verification_id)
            .order_by(VerificationDocumentRow.uploaded_at, VerificationDocumentRow.id)
            .execution_options(populate_existing=True)
        )
        return [self._doc(r) for r in rows]

    async def delete_document(self, document_id: uuid.UUID) -> None:
        await self._s.execute(
            delete(VerificationDocumentRow).where(VerificationDocumentRow.id == document_id)
        )

    async def mark_document_deleted(self, document_id: uuid.UUID, at: datetime) -> None:
        await self._s.execute(
            update(VerificationDocumentRow)
            .where(VerificationDocumentRow.id == document_id)
            .values(deleted_at=at)
        )

    async def documents_to_purge(self, decided_before: datetime) -> list[StoredDocument]:
        rows = await self._s.scalars(
            select(VerificationDocumentRow)
            .join(VerificationRow, VerificationRow.id == VerificationDocumentRow.verification_id)
            .where(
                VerificationDocumentRow.deleted_at.is_(None),
                VerificationRow.decided_at.is_not(None),
                VerificationRow.decided_at < decided_before,
            )
        )
        return [self._doc(r) for r in rows]

    async def all_documents_of_property(self, property_id: uuid.UUID) -> list[StoredDocument]:
        rows = await self._s.scalars(
            select(VerificationDocumentRow)
            .join(VerificationRow, VerificationRow.id == VerificationDocumentRow.verification_id)
            .where(VerificationRow.property_id == property_id)
        )
        return [self._doc(r) for r in rows]
