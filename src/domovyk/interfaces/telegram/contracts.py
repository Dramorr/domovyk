"""CTR-SIGN, запасний шлях: файл підпису з порталу Дії — у чат бота.

Основний шлях — вибір файлу в Mini App. Сюди ведуть з картки оренди посиланням
`/start sig_<contract_id>`, якщо вибір файлу в Mini App на телефоні не працює.
"""

from __future__ import annotations

import io
import uuid

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from domovyk.core.errors import (
    AccountInactive,
    Banned,
    Conflict,
    DomainError,
    InvalidInput,
    LeaseLimitReached,
    NotFound,
    NotRegistered,
    PermissionDenied,
)
from domovyk.domain.contracts import SIGNATURE_MAX_BYTES
from domovyk.interfaces.telegram.keyboards import open_app_keyboard
from domovyk.interfaces.telegram.texts import uk
from domovyk.modules.contracts.service import ContractsService

SIG_PREFIX = "sig_"
_SIGNATURE_EXT = (".p7s", ".p7m", ".asice", ".sce")


class ContractSign(StatesGroup):
    signature = State()


def _contract_id(payload: str | None) -> uuid.UUID | None:
    if not payload or not payload.startswith(SIG_PREFIX):
        return None
    try:
        return uuid.UUID(payload.removeprefix(SIG_PREFIX))
    except ValueError:
        return None


def make_contracts_router(mini_app_url: str) -> Router:
    router = Router(name="contracts")

    @router.message(CommandStart(deep_link=True, magic=F.args.startswith(SIG_PREFIX)))
    async def start_upload(message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.text is None:
            return
        contract_id = _contract_id(message.text.partition(" ")[2].strip())
        if contract_id is None:
            await message.answer(uk.SIG_BOT_NOT_FOUND)
            return
        await state.set_state(ContractSign.signature)
        await state.update_data(contract_id=str(contract_id))
        await message.answer(uk.SIG_BOT_SEND)

    @router.message(ContractSign.signature, F.document)
    async def receive_signature(
        message: Message, state: FSMContext, bot: Bot, contracts: ContractsService
    ) -> None:
        if message.from_user is None or message.document is None:
            return
        doc = message.document
        name = (doc.file_name or "").lower()
        if not name.endswith(_SIGNATURE_EXT) or (doc.file_size or 0) > SIGNATURE_MAX_BYTES:
            await message.answer(uk.SIG_BOT_NOT_SIGNATURE)
            return
        data = await state.get_data()
        contract_id = uuid.UUID(data["contract_id"])
        buffer = io.BytesIO()
        await bot.download(doc, destination=buffer)
        try:
            result = await contracts.upload_signature(
                message.from_user.id,
                contract_id,
                doc.file_name or "signature.p7s",
                buffer.getvalue(),
            )
        except InvalidInput as exc:
            reason = str(exc.details.get("reason") or "unreadable")
            await message.answer(uk.SIG_BOT_ERRORS.get(reason, uk.SIG_BOT_ERRORS["unreadable"]))
            return
        except (NotFound, NotRegistered, PermissionDenied) as exc:
            await state.clear()
            if isinstance(exc, Banned):
                await message.answer(uk.SIG_BOT_BANNED)
            else:
                await message.answer(uk.SIG_BOT_NOT_FOUND)
            return
        except AccountInactive:
            await state.clear()
            await message.answer(uk.ACCOUNT_BANNED)
            return
        except LeaseLimitReached:
            await state.clear()
            await message.answer(uk.SIG_BOT_LIMIT)
            return
        except Conflict:
            await state.clear()
            await message.answer(uk.SIG_BOT_NOT_ACCEPTING)
            return
        except DomainError:
            await message.answer(uk.SIG_BOT_ERROR)
            return
        await state.clear()
        signer = next((p for p in result.parties if p.is_me), None)
        signer_name = signer.signature.signer_name if signer and signer.signature else ""
        text = uk.SIG_BOT_ACCEPTED if result.lease_status == "active" else uk.SIG_BOT_RECEIVED
        await message.answer(
            text.format(name=signer_name or "—"),
            reply_markup=open_app_keyboard(mini_app_url, f"lease_{result.lease_id}"),
        )

    @router.message(ContractSign.signature)
    async def not_a_file(message: Message) -> None:
        await message.answer(uk.SIG_BOT_NOT_SIGNATURE)

    return router
