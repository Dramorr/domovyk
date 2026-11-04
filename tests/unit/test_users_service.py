"""Юніт-тести modules/users/service.py з фейковими репозиторіями (FR-USR-1..5)."""

from __future__ import annotations

import pytest

from domovyk.core.crypto import phone_hash
from domovyk.core.errors import (
    AlreadyRegistered,
    Banned,
    Conflict,
    ConsentOutdated,
    InvalidInput,
    NotAdmin,
    NotFound,
    NotRegistered,
    ProtectedRole,
)
from domovyk.domain.users import IdentityProvider, UserRole, UserStatus
from domovyk.modules.moderation.schemas import BanIdentifiers
from domovyk.modules.users.schemas import TenantProfile
from domovyk.modules.users.service import DELETED_NAME, UsersService
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V, TEST_HMAC, FakeDb, make_services

TG = 42
PHONE = "+380670000001"


async def _register(users: UsersService, tg: int = TG, name: str = "Тарас") -> None:
    await users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)


async def test_register_creates_user_with_consents_and_event() -> None:
    users, db = make_services()
    await _register(users, name="  Тарас   Шевченко ")
    user = await users.require_active(TG)
    assert user.display_name == "Тарас Шевченко"
    assert user.role is UserRole.USER
    assert user.has_phone is False
    assert {(d.value, v) for _, d, v in db.consents} == {("terms", TERMS_V), ("privacy", PRIVACY_V)}
    assert [name for _, name, _ in db.events] == ["registered"]


async def test_register_requires_current_consent_versions() -> None:
    users, _ = make_services()
    with pytest.raises(ConsentOutdated):
        await users.register(TG, "Тарас", terms_version="old", privacy_version=PRIVACY_V)


@pytest.mark.parametrize("name", ["Т", "x" * 41, "Тарас123", "@taras", "---"])
async def test_register_rejects_bad_names(name: str) -> None:
    users, _ = make_services()
    with pytest.raises(InvalidInput):
        await _register(users, name=name)


@pytest.mark.parametrize("name", ["Олег", "Анна-Марія", "О'Коннор", "Мар’яна", "Jean Luc"])
async def test_register_accepts_good_names(name: str) -> None:
    users, _ = make_services()
    await _register(users, name=name)


async def test_register_twice_conflicts() -> None:
    users, _ = make_services()
    await _register(users)
    with pytest.raises(AlreadyRegistered):
        await _register(users)


async def test_banned_telegram_cannot_register() -> None:
    users, db = make_services()
    await _register(users, tg=ADMIN_TG, name="Адмін")
    await users.ban(ADMIN_TG, TG, "посередник")
    with pytest.raises(Banned):
        await _register(users)
    assert not any(k == (IdentityProvider.TELEGRAM, str(TG)) for k in db.identities)


async def test_bootstrap_admin_from_config() -> None:
    users, db = make_services()
    await _register(users, tg=ADMIN_TG, name="Олег")
    assert (await users.require_active(ADMIN_TG)).role is UserRole.ADMIN
    assert db.audit[0]["action"] == "role.bootstrap"


async def test_bootstrap_admin_added_after_registration() -> None:
    db = FakeDb()
    users, _ = make_services(db, admin_telegram_ids=frozenset())
    await _register(users, tg=ADMIN_TG, name="Олег")
    users2, _ = make_services(db)  # тепер ID є в ADMIN_TELEGRAM_IDS
    assert (await users2.require_active(ADMIN_TG)).role is UserRole.ADMIN


async def test_add_phone_stores_e164_and_requires_registration() -> None:
    users, db = make_services()
    with pytest.raises(NotRegistered):
        await users.add_phone(TG, PHONE)
    await _register(users)
    user = await users.add_phone(TG, "380 67 000 00 01")
    assert user.has_phone
    assert (IdentityProvider.PHONE, PHONE) in db.identities


async def test_add_phone_taken_by_other_account() -> None:
    users, _ = make_services()
    await _register(users, tg=1)
    await users.add_phone(1, PHONE)
    await _register(users, tg=2)
    with pytest.raises(Conflict):
        await users.add_phone(2, PHONE)


async def test_banned_phone_blocks_new_account() -> None:
    """FR-USR-2: забанений номер під новим Telegram → новий акаунт теж забанено."""
    users, db = make_services()
    await _register(users, tg=ADMIN_TG, name="Адмін")
    await _register(users, tg=1)
    await users.add_phone(1, PHONE)
    await users.ban(ADMIN_TG, 1, "посередник")

    await _register(users, tg=2)  # той самий номер, новий Telegram
    with pytest.raises(Banned):
        await users.add_phone(2, PHONE)
    with pytest.raises(Banned):
        await users.require_active(2)
    newest = db.bans[-1]
    assert newest.reason == "reregistration_attempt"
    assert newest.ids == BanIdentifiers(telegram_uid="2", phone_hash=phone_hash(PHONE, TEST_HMAC))


async def test_admin_grants_and_revokes_moderator_with_audit() -> None:
    users, db = make_services()
    await _register(users, tg=ADMIN_TG, name="Олег")
    await _register(users, tg=TG)
    assert (await users.set_moderator(ADMIN_TG, TG, grant=True)).role is UserRole.MODERATOR
    assert (await users.set_moderator(ADMIN_TG, TG, grant=False)).role is UserRole.USER
    assert [a["action"] for a in db.audit][-2:] == ["role.grant", "role.revoke"]


async def test_only_admin_grants_roles() -> None:
    users, _ = make_services()
    await _register(users, tg=TG)
    await _register(users, tg=7)
    with pytest.raises(NotAdmin):
        await users.set_moderator(TG, 7, grant=True)


async def test_admin_role_is_protected() -> None:
    users, _ = make_services(admin_telegram_ids=frozenset({ADMIN_TG, 77}))
    await _register(users, tg=ADMIN_TG, name="Олег")
    await _register(users, tg=77, name="Друга")
    with pytest.raises(ProtectedRole):
        await users.set_moderator(ADMIN_TG, 77, grant=False)
    with pytest.raises(ProtectedRole):
        await users.ban(ADMIN_TG, 77, "тест")


async def test_grant_unknown_user() -> None:
    users, _ = make_services()
    await _register(users, tg=ADMIN_TG, name="Олег")
    with pytest.raises(NotFound):
        await users.set_moderator(ADMIN_TG, 999, grant=True)


async def test_ban_and_unban_registered_user() -> None:
    users, db = make_services()
    await _register(users, tg=ADMIN_TG, name="Олег")
    await _register(users, tg=TG)
    await users.add_phone(TG, PHONE)
    await users.ban(ADMIN_TG, TG, "посередник")
    with pytest.raises(Banned):
        await users.require_active(TG)
    assert db.bans[-1].ids.phone_hash == phone_hash(PHONE, TEST_HMAC)
    assert await users.unban(ADMIN_TG, TG) == 1
    assert (await users.require_active(TG)).status is UserStatus.ACTIVE


async def test_tenant_profile_roundtrip_and_cleanup() -> None:
    users, _ = make_services()
    await _register(users)
    saved = await users.update_tenant_profile(
        TG,
        TenantProfile(about="  Тиха пара ", household_size=2, has_pets=False, pets_details="кіт"),
    )
    assert saved.about == "Тиха пара"
    assert saved.pets_details is None  # без тварин — без опису тварин
    assert (await users.get_tenant_profile(TG)).household_size == 2


async def test_delete_account_anonymizes_and_allows_reregistration() -> None:
    users, db = make_services()
    await _register(users)
    await users.add_phone(TG, PHONE)
    await users.update_tenant_profile(TG, TenantProfile(about="Про мене"))
    await users.delete_account(TG)

    (deleted,) = db.users.values()
    assert deleted.status is UserStatus.DELETED
    assert deleted.display_name == DELETED_NAME
    assert not db.identities and not db.profiles
    assert await users.find_by_telegram(TG) is None
    await _register(users)  # нова людина з тим самим Telegram — новий акаунт
