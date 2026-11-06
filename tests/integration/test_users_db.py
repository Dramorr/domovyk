"""Модуль users на справжньому Postgres: запити репозиторіїв і обмеження схеми."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.core.errors import Banned, Conflict
from domovyk.domain.users import UserRole, UserStatus
from domovyk.modules.users.schemas import TenantProfile
from domovyk.modules.users.service import UsersService
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V

pytestmark = pytest.mark.integration

PHONE = "+380670000001"


async def _register(users: UsersService, tg: int, name: str = "Тарас") -> None:
    await users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)


async def test_full_account_cycle(users: UsersService, engine: AsyncEngine) -> None:
    await _register(users, ADMIN_TG, "Олег")
    await _register(users, 42)
    user = await users.add_phone(42, PHONE)
    assert user.has_phone and user.telegram_uid == 42

    assert (await users.set_moderator(ADMIN_TG, 42, grant=True)).role is UserRole.MODERATOR
    await users.update_tenant_profile(42, TenantProfile(about="Тиха пара", household_size=2))
    assert (await users.get_tenant_profile(42)).household_size == 2

    async with engine.connect() as conn:
        actions = (await conn.execute(text("SELECT action FROM audit_log ORDER BY id"))).scalars()
        assert list(actions) == ["role.bootstrap", "role.grant"]
        consents = await conn.scalar(text("SELECT count(*) FROM user_consents"))
        assert consents == 4
        events = await conn.scalar(text("SELECT count(*) FROM events WHERE name='registered'"))
        assert events == 2


async def test_ban_by_phone_hash_blocks_new_telegram(
    users: UsersService, engine: AsyncEngine
) -> None:
    await _register(users, ADMIN_TG, "Олег")
    await _register(users, 1)
    await users.add_phone(1, PHONE)
    await users.ban(ADMIN_TG, 1, "посередник")
    await _register(users, 2)
    with pytest.raises(Banned):
        await users.add_phone(2, PHONE)
    assert (await users.find_by_telegram(2)).status is UserStatus.BANNED  # type: ignore[union-attr]

    async with engine.connect() as conn:
        # Телефон у банах — лише хеш, не номер.
        assert await conn.scalar(text("SELECT count(*) FROM bans WHERE phone_hash LIKE '+%'")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM bans")) == 2


async def test_phone_unique_across_accounts(users: UsersService) -> None:
    await _register(users, 1)
    await users.add_phone(1, PHONE)
    await _register(users, 2)
    with pytest.raises(Conflict):
        await users.add_phone(2, PHONE)


async def test_delete_account_keeps_no_personal_data(
    users: UsersService, engine: AsyncEngine
) -> None:
    await _register(users, 42)
    await users.add_phone(42, PHONE)
    await users.update_tenant_profile(42, TenantProfile(about="Про мене"))
    await users.delete_account(42)
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM auth_identities")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM tenant_profiles")) == 0
        row = (await conn.execute(text("SELECT display_name, status FROM users"))).one()
        assert row == ("deleted", "deleted")
    await _register(users, 42)  # можна зареєструватись заново


@pytest.mark.parametrize(
    "sql",
    [
        # household_size лише 1..20
        "INSERT INTO tenant_profiles (user_id, household_size) VALUES (:uid, 0)",
        # одна й та сама ідентичність не може належати двом акаунтам
        "INSERT INTO auth_identities (user_id, provider, provider_uid) "
        "VALUES (:uid, 'telegram', '42')",
        # бан без жодного ідентифікатора заборонено
        "INSERT INTO bans (reason) VALUES ('x')",
        # роль — лише з enum
        "UPDATE users SET role = 'owner' WHERE id = :uid",
    ],
)
async def test_db_constraints(users: UsersService, engine: AsyncEngine, sql: str) -> None:
    await _register(users, 42)
    user = await users.find_by_telegram(42)
    assert user is not None
    async with engine.connect() as conn:
        with pytest.raises(Exception) as exc:
            await conn.execute(text(sql), {"uid": user.id})
        assert isinstance(exc.value, IntegrityError) or "invalid input value" in str(exc.value)


async def test_app_mode_db(users: UsersService, engine: AsyncEngine) -> None:
    from domovyk.domain.users import AppMode

    await _register(users, 42)
    await users.set_mode(42, AppMode.LANDLORD)
    assert (await users.find_by_telegram(42)).mode is AppMode.LANDLORD  # type: ignore[union-attr]
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(text("UPDATE users SET app_mode = 'agent'"))
