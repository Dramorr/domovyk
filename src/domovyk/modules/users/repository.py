"""Доступ до users, auth_identities, tenant_profiles, user_consents. Лише всередині модуля."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert

from domovyk.core.tx import Tx, session_of
from domovyk.domain.users import AppMode, ConsentDocument, IdentityProvider, UserRole, UserStatus
from domovyk.modules.users.models import (
    AuthIdentityRow,
    TenantProfileRow,
    UserConsentRow,
    UserRow,
)
from domovyk.modules.users.schemas import PersonalIn, TenantProfile, UserOut


class UsersRepository(Protocol):
    async def get(self, user_id: uuid.UUID) -> UserOut | None: ...
    async def get_by_identity(self, provider: IdentityProvider, uid: str) -> UserOut | None: ...
    async def create(self, display_name: str, role: UserRole) -> uuid.UUID: ...
    async def add_identity(
        self,
        user_id: uuid.UUID,
        provider: IdentityProvider,
        uid: str,
        *,
        is_contact: bool,
        verified_at: datetime | None,
    ) -> None: ...
    async def identity_uid(self, user_id: uuid.UUID, provider: IdentityProvider) -> str | None: ...
    async def remove_identities(
        self, user_id: uuid.UUID, providers: list[IdentityProvider] | None = None
    ) -> None: ...
    async def set_role(self, user_id: uuid.UUID, role: UserRole) -> None: ...
    async def set_status(
        self, user_id: uuid.UUID, status: UserStatus, *, deleted_at: datetime | None = None
    ) -> None: ...
    async def set_display_name(self, user_id: uuid.UUID, name: str) -> None: ...
    async def set_mode(self, user_id: uuid.UUID, mode: AppMode) -> None: ...
    async def add_consent(
        self, user_id: uuid.UUID, document: ConsentDocument, version: str, at: datetime
    ) -> None: ...
    async def get_tenant_profile(self, user_id: uuid.UUID) -> TenantProfile | None: ...
    async def upsert_tenant_profile(self, user_id: uuid.UUID, profile: TenantProfile) -> None: ...
    async def delete_tenant_profile(self, user_id: uuid.UUID) -> None: ...
    async def list_staff_ids(self) -> list[uuid.UUID]: ...
    async def get_personal(self, user_id: uuid.UUID) -> PersonalIn: ...
    async def set_personal(self, user_id: uuid.UUID, data: PersonalIn) -> None: ...
    async def avatar_key(self, user_id: uuid.UUID) -> str | None: ...
    async def set_avatar(
        self, user_id: uuid.UUID, key: str | None, at: datetime | None
    ) -> None: ...


class SqlUsersRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def _to_out(self, row: UserRow) -> UserOut:
        tg = await self.identity_uid(row.id, IdentityProvider.TELEGRAM)
        has_phone = bool(
            await self._s.scalar(
                select(
                    exists().where(
                        AuthIdentityRow.user_id == row.id,
                        AuthIdentityRow.provider == IdentityProvider.PHONE,
                    )
                )
            )
        )
        return UserOut(
            id=row.id,
            display_name=row.display_name,
            role=row.role,
            status=row.status,
            telegram_uid=int(tg) if tg else None,
            has_phone=has_phone,
            created_at=row.created_at,
            mode=AppMode(row.app_mode) if row.app_mode else None,
            avatar_version=_version(row.avatar_updated_at) if row.avatar_key else None,
        )

    async def get(self, user_id: uuid.UUID) -> UserOut | None:
        row = await self._s.get(UserRow, user_id, populate_existing=True)
        return await self._to_out(row) if row else None

    async def get_by_identity(self, provider: IdentityProvider, uid: str) -> UserOut | None:
        row = await self._s.scalar(
            select(UserRow)
            .join(AuthIdentityRow, AuthIdentityRow.user_id == UserRow.id)
            .where(AuthIdentityRow.provider == provider, AuthIdentityRow.provider_uid == uid)
            .execution_options(populate_existing=True)
        )
        return await self._to_out(row) if row else None

    async def create(self, display_name: str, role: UserRole) -> uuid.UUID:
        row = UserRow(display_name=display_name, role=role)
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return row.id

    async def add_identity(
        self,
        user_id: uuid.UUID,
        provider: IdentityProvider,
        uid: str,
        *,
        is_contact: bool,
        verified_at: datetime | None,
    ) -> None:
        self._s.add(
            AuthIdentityRow(
                user_id=user_id,
                provider=provider,
                provider_uid=uid,
                is_contact=is_contact,
                verified_at=verified_at,
            )
        )
        await self._s.flush()

    async def identity_uid(self, user_id: uuid.UUID, provider: IdentityProvider) -> str | None:
        return await self._s.scalar(
            select(AuthIdentityRow.provider_uid).where(
                AuthIdentityRow.user_id == user_id, AuthIdentityRow.provider == provider
            )
        )

    async def remove_identities(
        self, user_id: uuid.UUID, providers: list[IdentityProvider] | None = None
    ) -> None:
        stmt = delete(AuthIdentityRow).where(AuthIdentityRow.user_id == user_id)
        if providers:
            stmt = stmt.where(AuthIdentityRow.provider.in_(providers))
        await self._s.execute(stmt)

    async def set_role(self, user_id: uuid.UUID, role: UserRole) -> None:
        await self._s.execute(update(UserRow).where(UserRow.id == user_id).values(role=role))

    async def set_status(
        self, user_id: uuid.UUID, status: UserStatus, *, deleted_at: datetime | None = None
    ) -> None:
        values: dict[str, object] = {"status": status}
        if deleted_at is not None:
            values["deleted_at"] = deleted_at
        await self._s.execute(update(UserRow).where(UserRow.id == user_id).values(**values))

    async def set_mode(self, user_id: uuid.UUID, mode: AppMode) -> None:
        await self._s.execute(
            update(UserRow).where(UserRow.id == user_id).values(app_mode=mode.value)
        )

    async def set_display_name(self, user_id: uuid.UUID, name: str) -> None:
        await self._s.execute(
            update(UserRow).where(UserRow.id == user_id).values(display_name=name)
        )

    async def add_consent(
        self, user_id: uuid.UUID, document: ConsentDocument, version: str, at: datetime
    ) -> None:
        self._s.add(
            UserConsentRow(user_id=user_id, document=document, version=version, accepted_at=at)
        )
        await self._s.flush()

    async def get_tenant_profile(self, user_id: uuid.UUID) -> TenantProfile | None:
        row = await self._s.get(TenantProfileRow, user_id, populate_existing=True)
        if row is None:
            return None
        return TenantProfile(
            about=row.about,
            occupation=row.occupation,
            household_size=row.household_size,
            has_children=row.has_children,
            has_pets=row.has_pets,
            pets_details=row.pets_details,
            smoker=row.smoker,
        )

    async def upsert_tenant_profile(self, user_id: uuid.UUID, profile: TenantProfile) -> None:
        values = profile.model_dump()
        stmt = insert(TenantProfileRow).values(user_id=user_id, **values)
        await self._s.execute(
            stmt.on_conflict_do_update(index_elements=[TenantProfileRow.user_id], set_=values)
        )

    async def delete_tenant_profile(self, user_id: uuid.UUID) -> None:
        await self._s.execute(delete(TenantProfileRow).where(TenantProfileRow.user_id == user_id))

    async def get_personal(self, user_id: uuid.UUID) -> PersonalIn:
        row = await self._s.get(UserRow, user_id, populate_existing=True)
        if row is None:
            return PersonalIn()
        return PersonalIn(
            legal_name=row.legal_name, birth_date=row.birth_date, city_code=row.city_code
        )

    async def set_personal(self, user_id: uuid.UUID, data: PersonalIn) -> None:
        await self._s.execute(
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(
                legal_name=data.legal_name, birth_date=data.birth_date, city_code=data.city_code
            )
        )

    async def avatar_key(self, user_id: uuid.UUID) -> str | None:
        return await self._s.scalar(select(UserRow.avatar_key).where(UserRow.id == user_id))

    async def set_avatar(self, user_id: uuid.UUID, key: str | None, at: datetime | None) -> None:
        await self._s.execute(
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(avatar_key=key, avatar_updated_at=at)
        )

    async def list_staff_ids(self) -> list[uuid.UUID]:
        rows = await self._s.scalars(
            select(UserRow.id).where(
                UserRow.role.in_([UserRole.MODERATOR, UserRole.ADMIN]),
                UserRow.status == UserStatus.ACTIVE,
            )
        )
        return list(rows)


def _version(at: datetime | None) -> str | None:
    return str(int(at.timestamp())) if at else None
