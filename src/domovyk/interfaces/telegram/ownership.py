"""PRP-OWN, запасний шлях: довідка з реєстру прав файлом у чат бота.

Основний шлях — вибір файлу в Mini App. Сюди ведуть з картки об'єкта кнопкою
«Надіслати через бота» (deep link `/start own_<property_id>`), якщо вибір файлу
в Mini App на телефоні не працює (SPEC, відкрите питання 9).
"""

from __future__ import annotations

import io
import uuid
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from domovyk.core.errors import (
    AccountInactive,
    Banned,
    Conflict,
    DomainError,
    InvalidInput,
    NotFound,
    NotRegistered,
    TooManyDocuments,
)
from domovyk.interfaces.telegram.keyboards import open_app_keyboard
from domovyk.interfaces.telegram.notifications import format_address
from domovyk.interfaces.telegram.texts import uk
from domovyk.modules.analytics.service import EventSource
from domovyk.modules.properties.service import PropertiesService

OWN_PREFIX = "own_"


class Ownership(StatesGroup):
    document = State()


class OwnAction(CallbackData, prefix="own"):
    action: str  # submit | cancel


def _actions_keyboard(with_submit: bool) -> InlineKeyboardMarkup:
    row = []
    if with_submit:
        row.append(
            InlineKeyboardButton(
                text=uk.BTN_OWN_SUBMIT, callback_data=OwnAction(action="submit").pack()
            )
        )
    row.append(
        InlineKeyboardButton(text=uk.BTN_CANCEL, callback_data=OwnAction(action="cancel").pack())
    )
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _property_id(payload: str | None) -> uuid.UUID | None:
    if not payload or not payload.startswith(OWN_PREFIX):
        return None
    try:
        return uuid.UUID(payload.removeprefix(OWN_PREFIX))
    except ValueError:
        return None


def make_ownership_router(mini_app_url: str, *, max_mb: int, retention_days: int) -> Router:
    router = Router(name="ownership")

    @router.message(CommandStart(deep_link=True, magic=F.args.startswith(OWN_PREFIX)))
    async def start_upload(
        message: Message, state: FSMContext, properties: PropertiesService
    ) -> None:
        if message.from_user is None or message.text is None:
            return
        payload = message.text.partition(" ")[2].strip()
        property_id = _property_id(payload)
        if property_id is None:
            await message.answer(uk.OWN_BOT_NOT_FOUND)
            return
        try:
            prop = await properties.get_mine(message.from_user.id, property_id)
        except (NotFound, NotRegistered):
            await message.answer(uk.OWN_BOT_NOT_FOUND)
            return
        except (Banned, AccountInactive):
            await message.answer(uk.ACCOUNT_BANNED)
            return
        await state.set_state(Ownership.document)
        await state.update_data(property_id=str(property_id))
        address = format_address(
            {"street": prop.street, "house_number": prop.house_number, "apartment": prop.apartment}
        )
        await message.answer(
            uk.OWN_BOT_SEND_PDF.format(address=address, days=retention_days),
            reply_markup=_actions_keyboard(with_submit=False),
        )

    @router.message(Ownership.document, F.document)
    async def receive_document(
        message: Message, state: FSMContext, bot: Bot, properties: PropertiesService
    ) -> None:
        if message.from_user is None or message.document is None:
            return
        doc = message.document
        is_pdf = (doc.mime_type == "application/pdf") or (doc.file_name or "").lower().endswith(
            ".pdf"
        )
        if not is_pdf or (doc.file_size or 0) > max_mb * 1024 * 1024:
            await message.answer(uk.OWN_BOT_NOT_PDF.format(mb=max_mb))
            return
        data = await state.get_data()
        property_id = uuid.UUID(data["property_id"])
        buffer = io.BytesIO()
        await bot.download(doc, destination=buffer)
        try:
            result = await properties.upload_document(
                message.from_user.id,
                property_id,
                doc.file_name or "document.pdf",
                buffer.getvalue(),
            )
        except NotFound:
            await state.clear()
            await message.answer(uk.OWN_BOT_NOT_FOUND)
            return
        except Conflict:
            await state.clear()
            await message.answer(uk.OWN_BOT_NOT_ACCEPTING)
            return
        except TooManyDocuments:
            await message.answer(uk.OWN_BOT_TOO_MANY, reply_markup=_actions_keyboard(True))
            return
        except InvalidInput:
            await message.answer(uk.OWN_BOT_NOT_PDF.format(mb=max_mb))
            return
        except DomainError:
            await message.answer(uk.OWN_BOT_ERROR)
            return
        count = len(result.verification.documents) if result.verification else 0
        await message.answer(
            uk.OWN_BOT_RECEIVED.format(filename=escape(doc.file_name or "PDF"), count=count),
            reply_markup=_actions_keyboard(with_submit=True),
        )

    @router.message(Ownership.document)
    async def not_a_document(message: Message) -> None:
        await message.answer(uk.OWN_BOT_NOT_PDF.format(mb=max_mb))

    @router.callback_query(OwnAction.filter(F.action == "submit"))
    async def submit(
        callback: CallbackQuery, state: FSMContext, properties: PropertiesService
    ) -> None:
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        data = await state.get_data()
        if "property_id" not in data:
            await callback.message.answer(uk.OWN_BOT_NOT_FOUND)
            return
        try:
            await properties.submit_ownership(
                callback.from_user.id, uuid.UUID(data["property_id"]), source=EventSource.BOT
            )
        except InvalidInput:
            await callback.message.answer(uk.OWN_BOT_NOTHING)
            return
        except (Conflict, NotFound):
            await state.clear()
            await callback.message.answer(uk.OWN_BOT_NOT_ACCEPTING)
            return
        await state.clear()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            uk.OWN_BOT_SUBMITTED,
            reply_markup=open_app_keyboard(mini_app_url, f"prop_{data['property_id']}"),
        )

    @router.callback_query(OwnAction.filter(F.action == "cancel"))
    async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.clear()
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(uk.OWN_BOT_CANCELLED)

    return router
