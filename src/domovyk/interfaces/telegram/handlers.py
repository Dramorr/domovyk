"""Хендлери бота. Тонкі: тексти з texts/uk.py, логіка — у сервісах модулів.

Сервіси приходять через workflow data диспетчера (`users=...`).
"""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message, ReplyKeyboardRemove

from domovyk.core.errors import (
    AccountInactive,
    Banned,
    Conflict,
    InvalidInput,
    NotFound,
    NotRegistered,
    PermissionDenied,
    ProtectedRole,
)
from domovyk.domain.users import UserStatus
from domovyk.interfaces.telegram.keyboards import open_app_keyboard, share_contact_keyboard
from domovyk.interfaces.telegram.texts import uk
from domovyk.modules.users.service import UsersService


def _role_name(role: str) -> str:
    return uk.ROLE_NAMES.get(role, role)


def _target_id(command: CommandObject) -> tuple[int | None, str]:
    """Перший аргумент — Telegram ID, решта — текст (причина)."""
    parts = (command.args or "").split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        return None, ""
    return int(parts[0]), parts[1].strip() if len(parts) > 1 else ""


def make_router(mini_app_url: str) -> Router:
    router = Router(name="entry")

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject, users: UsersService) -> None:
        if message.from_user is None:
            return
        user = await users.find_by_telegram(message.from_user.id)
        if user is not None and user.status is UserStatus.BANNED:
            await message.answer(uk.ACCOUNT_BANNED)
            return
        if user is not None and user.status is not UserStatus.DELETED:
            text = uk.ONB_WELCOME_BACK.format(name=escape(user.display_name))
        else:
            text = uk.ONB_WELCOME.format(name=escape(message.from_user.first_name))
        await message.answer(text, reply_markup=open_app_keyboard(mini_app_url, command.args))

    @router.message(Command("menu"))
    async def menu(message: Message) -> None:
        await message.answer(uk.MENU_MAIN, reply_markup=open_app_keyboard(mini_app_url))

    @router.message(Command("phone"))
    async def phone(message: Message) -> None:
        await message.answer(uk.PHONE_ASK, reply_markup=share_contact_keyboard())

    @router.message(F.contact)
    async def contact(message: Message, users: UsersService) -> None:
        """Контакт надходить від Telegram: з кнопки бота або з requestContact у Mini App."""
        if message.from_user is None or message.contact is None:
            return
        if message.contact.user_id != message.from_user.id:
            await message.answer(uk.PHONE_NOT_YOURS, reply_markup=share_contact_keyboard())
            return
        try:
            await users.add_phone(message.from_user.id, message.contact.phone_number)
        except Banned:
            await message.answer(uk.ACCOUNT_BANNED, reply_markup=ReplyKeyboardRemove())
        except AccountInactive:
            await message.answer(uk.ACCOUNT_INACTIVE, reply_markup=ReplyKeyboardRemove())
        except NotRegistered:
            await message.answer(uk.PHONE_REGISTER_FIRST, reply_markup=ReplyKeyboardRemove())
            await message.answer(uk.MENU_MAIN, reply_markup=open_app_keyboard(mini_app_url))
        except Conflict:
            await message.answer(uk.PHONE_TAKEN, reply_markup=ReplyKeyboardRemove())
        except InvalidInput:
            await message.answer(uk.PHONE_INVALID, reply_markup=share_contact_keyboard())
        else:
            await message.answer(uk.PHONE_SAVED, reply_markup=ReplyKeyboardRemove())
            await message.answer(uk.MENU_MAIN, reply_markup=open_app_keyboard(mini_app_url))

    @router.message(Command("whoami"))
    async def whoami(message: Message, users: UsersService) -> None:
        if message.from_user is None:
            return
        tg_id = message.from_user.id
        user = await users.find_by_telegram(tg_id)
        if user is None:
            await message.answer(uk.WHOAMI_UNREGISTERED.format(tg_id=tg_id))
        else:
            await message.answer(uk.WHOAMI.format(tg_id=tg_id, role=_role_name(user.role)))

    async def _set_moderator(
        message: Message, command: CommandObject, users: UsersService, *, grant: bool
    ) -> None:
        if message.from_user is None:
            return
        target, _ = _target_id(command)
        if target is None:
            await message.answer(uk.ADMIN_USAGE_ROLE.format(cmd=command.command))
            return
        try:
            user = await users.set_moderator(message.from_user.id, target, grant=grant)
        except NotFound:
            await message.answer(uk.ADMIN_USER_NOT_FOUND)
        except ProtectedRole:
            await message.answer(uk.ADMIN_CANT_CHANGE_ADMIN)
        except (NotRegistered, PermissionDenied):
            await message.answer(uk.ADMIN_ONLY)
        else:
            await message.answer(
                uk.ADMIN_ROLE_CHANGED.format(
                    name=escape(user.display_name), role=_role_name(user.role)
                )
            )

    @router.message(Command("grant_mod"))
    async def grant_mod(message: Message, command: CommandObject, users: UsersService) -> None:
        await _set_moderator(message, command, users, grant=True)

    @router.message(Command("revoke_mod"))
    async def revoke_mod(message: Message, command: CommandObject, users: UsersService) -> None:
        await _set_moderator(message, command, users, grant=False)

    @router.message(Command("ban"))
    async def ban(message: Message, command: CommandObject, users: UsersService) -> None:
        if message.from_user is None:
            return
        target, reason = _target_id(command)
        if target is None or not reason:
            await message.answer(uk.ADMIN_USAGE_BAN)
            return
        try:
            await users.ban(message.from_user.id, target, reason)
        except ProtectedRole:
            await message.answer(uk.ADMIN_CANT_CHANGE_ADMIN)
        except (PermissionDenied, NotRegistered):
            await message.answer(uk.ADMIN_ONLY)
        except InvalidInput as exc:
            await message.answer(uk.ADMIN_BAD_INPUT.format(reason=escape(str(exc))))
        else:
            await message.answer(uk.ADMIN_BANNED.format(tg_id=target, reason=escape(reason)))

    @router.message(Command("unban"))
    async def unban(message: Message, command: CommandObject, users: UsersService) -> None:
        if message.from_user is None:
            return
        target, _ = _target_id(command)
        if target is None:
            await message.answer(uk.ADMIN_USAGE_ROLE.format(cmd="unban"))
            return
        try:
            count = await users.unban(message.from_user.id, target)
        except (PermissionDenied, NotRegistered):
            await message.answer(uk.ADMIN_ONLY)
        else:
            await message.answer(uk.ADMIN_UNBANNED.format(count=count))

    return router
