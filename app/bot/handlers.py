from datetime import datetime, date
from aiogram import Router, F, Bot
from aiogram.types import Message, PhotoSize, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.filters.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import asyncio
import re

from app.db.models import Esim, EsimStatus, User
from app.db.database import async_session_maker
from app.utils.qr_reader import read_qr_code
from app.utils.config import config
from app.bot.filters import AllowedUserFilter
from app.services import google_sheets

router = Router()
group_router = Router()

# Only allow private chat interactions - bot ignores all group messages
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

# Group router - NO filters, for callbacks in group chats (QA buttons, etc.)

storage = MemoryStorage()


class UploadState(StatesGroup):
    waiting_for_photo = State()
    waiting_for_provider = State()
    selecting_supplier = State()
    waiting_for_supplier_name = State()


class GetEsimState(StatesGroup):
    selecting_provider = State()


class AdminState(StatesGroup):
    waiting_for_add_user_id = State()
    waiting_for_remove_user_id = State()


class SipAttachState(StatesGroup):
    waiting_for_sip_number = State()


class ReserveSyncState(StatesGroup):
    waiting_for_reserve_count = State()


PROVIDERS = ["МТС", "Билайн", "Мегафон", "Tele2", "Другие"]

PROVIDER_MAPPING = {
    "esim.mts.ru": "МТС",
    "mno-02.esimservices.com": "Билайн",
    "mno-0b.esimservices.com": "Йота",
    "mno-04.esimservices.com": "Мегафон",
    "smdp.alfa.edu.az": "Алфа",
    "t2.toprsp.com": "Tele2",
}


def extract_smdp_domain(lpa_string: str) -> str | None:
    parts = lpa_string.split("$")
    if len(parts) >= 2:
        return parts[1].lower()
    return None


def get_provider_from_domain(lpa_string: str) -> str | None:
    domain = extract_smdp_domain(lpa_string)
    if domain:
        return PROVIDER_MAPPING.get(domain)
    return None


_album_data: dict[str, dict] = {}


async def broadcast_test_esim(provider: str, bot: Bot):
    user_ids = set(config.allowed_users)
    
    async with async_session_maker() as session:
        result = await session.execute(select(User.telegram_id))
        db_user_ids = result.scalars().all()
        user_ids.update(db_user_ids)
    
    message_text = f"⚠️ <b>Внимание!</b> Загружена новая тестовая eSIM (Оператор: {provider}). Пожалуйста, проверьте её в меню получения."
    
    for user_id in user_ids:
        try:
            await bot.send_message(chat_id=user_id, text=message_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            print(f"Failed to send notification to user {user_id}: {e}")





def get_main_menu(is_admin: bool = False):
    keyboard = [
        [KeyboardButton(text="📥 Загрузить eSIM"), KeyboardButton(text="🧪 Загрузить тестовую")],
        [KeyboardButton(text="📤 Получить eSIM"), KeyboardButton(text="📊 Статистика")],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, keyboard=keyboard)


def get_provider_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=provider, callback_data=f"provider:{provider}")]
        for provider in PROVIDERS
    ])


def get_provider_stock_buttons(stock_dict: dict):
    buttons = []
    for provider, count in stock_dict.items():
        if count > 0:
            buttons.append([InlineKeyboardButton(text=f"{provider} ({count} шт.)", callback_data=f"get_provider:{provider}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_issued_esim_keyboard(esim_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Привязать сип", callback_data=f"attach_sip:{esim_id}"), InlineKeyboardButton(text="📦 Резерв +", callback_data=f"to_reserve:{esim_id}")],
        [InlineKeyboardButton(text="🔙 Вернуть в базу", callback_data=f"return:{esim_id}")],
        [InlineKeyboardButton(text="🚫 Не ворк", callback_data=f"dead_esim:{esim_id}")],
    ])


def get_stats_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить активность за сегодня", callback_data="reset_stats")],
        [InlineKeyboardButton(text="📊 Сверка резерва", callback_data="menu_reserve_sync")],
    ])


def get_reset_confirmation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, обновить", callback_data="confirm_reset_stats")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel_reset_stats")],
    ])


def get_test_result_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Не ворк", callback_data="test_esim_dead")],
    ])


def get_supplier_selection_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Загрузить eSIM (наш)", callback_data="supplier_ours")],
        [InlineKeyboardButton(text="🛒 Загрузить eSIM (поставщик)", callback_data="supplier_other")],
    ])


@router.message(Command("start"), AllowedUserFilter())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    is_admin = message.from_user.id in config.allowed_users
    await message.answer(
        "Добро пожаловать в меню eSIM менеджера!",
        reply_markup=get_main_menu(is_admin)
    )


@router.message(F.text == "📥 Загрузить eSIM", AllowedUserFilter())
async def upload_esim_start(message: Message, state: FSMContext):
    try:
        print(f"Upload eSIM handler triggered for user {message.from_user.id}")
        await state.clear()
        await state.update_data(is_test=False)
        await message.answer(
            "Выберите тип загрузки:",
            reply_markup=get_supplier_selection_keyboard()
        )
        await state.set_state(UploadState.selecting_supplier)
    except Exception as e:
        print(f"ERROR in upload_esim_start: {e}")
        await message.answer(f"Ошибка: {e}")


@router.message(F.text == "🧪 Загрузить тестовую", AllowedUserFilter())
async def upload_test_esim_start(message: Message, state: FSMContext):
    try:
        print(f"Upload test eSIM handler triggered for user {message.from_user.id}")
        await state.clear()
        await state.update_data(is_test=True)
        await message.answer(
            "Выберите тип загрузки:",
            reply_markup=get_supplier_selection_keyboard()
        )
        await state.set_state(UploadState.selecting_supplier)
    except Exception as e:
        print(f"ERROR in upload_test_esim_start: {e}")
        await message.answer(f"Ошибка: {e}")


@router.callback_query(F.data == "supplier_ours", AllowedUserFilter())
async def process_supplier_ours(callback: CallbackQuery, state: FSMContext):
    print(f"!!! HANDLER process_supplier_ours called for user {callback.from_user.id}")
    # Preserve is_test flag before clearing state
    state_data = await state.get_data()
    is_test = state_data.get("is_test", False)
    await state.clear()
    await state.update_data(supplier=config.our_supplier_name, is_test=is_test)
    try:
        await callback.message.edit_text(
            f"✅ Выбран поставщик: {config.our_supplier_name}\n\n"
            "Теперь отправьте QR-коды eSIM (фото или документ).",
            reply_markup=None
        )
    except Exception as e:
        print(f"edit_text failed: {e}, using answer instead")
        await callback.message.answer(
            f"✅ Выбран поставщик: {config.our_supplier_name}\n\n"
            "Теперь отправьте QR-коды eSIM (фото или документ)."
        )
    await state.set_state(UploadState.waiting_for_photo)
    await callback.answer()


@router.callback_query(F.data == "supplier_other", AllowedUserFilter())
async def process_supplier_other(callback: CallbackQuery, state: FSMContext):
    print(f"!!! HANDLER process_supplier_other called for user {callback.from_user.id}")
    # Preserve is_test flag before clearing state
    state_data = await state.get_data()
    is_test = state_data.get("is_test", False)
    await state.clear()
    await state.update_data(is_test=is_test)
    try:
        try:
            await callback.message.edit_text(
                "Введите имя поставщика (например, @supplier_name):",
                reply_markup=None
            )
        except Exception as e:
            print(f"edit_text failed: {e}, using answer")
            await callback.message.answer(
                "Введите имя поставщика (например, @supplier_name):"
            )
        await state.set_state(UploadState.waiting_for_supplier_name)
        await callback.answer()
    except Exception as e:
        print(f"Error in process_supplier_other: {e}")


@router.message(UploadState.waiting_for_supplier_name, AllowedUserFilter())
async def process_supplier_name_input(message: Message, state: FSMContext):
    try:
        current_state = await state.get_state()
        print(f"Current state: {current_state}")
        
        supplier_name = message.text.strip()
        
        if not supplier_name.startswith('@'):
            supplier_name = f"@{supplier_name}"
        
        await state.update_data(supplier=supplier_name)
        is_admin = message.from_user.id in config.allowed_users
        await message.answer(
            f"✅ Поставщик: {supplier_name}\n\n"
            "Теперь отправьте QR-коды eSIM (фото или документ).",
            reply_markup=get_main_menu(is_admin)
        )
        await state.set_state(UploadState.waiting_for_photo)
    except Exception as e:
        print(f"Error in process_supplier_name_input: {e}")
        await message.answer(f"Ошибка: {e}")


@router.message(F.text == "📤 Получить eSIM", AllowedUserFilter())
async def get_esim_start(message: Message):
    stock = await get_stock_by_provider(is_test=False)
    test_stock = await get_stock_by_provider(is_test=True)
    test_total = sum(test_stock.values())
    
    buttons = []
    for provider, count in stock.items():
        if count > 0:
            buttons.append([InlineKeyboardButton(text=f"{provider} ({count} шт.)", callback_data=f"get_provider:{provider}")])
    
    if test_total > 0:
        buttons.append([InlineKeyboardButton(text=f"🧪 Тестовые eSIM ({test_total} шт.)", callback_data="get_test_providers")])
    
    if not buttons:
        await message.answer("Нет доступных eSIM в базе.")
    else:
        await message.answer("Выберите оператора:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


def format_provider_list(provider_counts: dict) -> str:
    if not provider_counts:
        return ""
    providers = list(provider_counts.items())
    lines = []
    for i, (provider, count) in enumerate(providers):
        prefix = " ├ " if i < len(providers) - 1 else " └ "
        lines.append(f"{prefix}{provider}: {count}")
    return "\n".join(lines)


@router.message(F.text == "📊 Статистика", AllowedUserFilter())
async def show_stats(message: Message):
    today = date.today()
    
    async with async_session_maker() as session:
        total_result = await session.execute(
            select(func.count(Esim.id)).where(Esim.status == EsimStatus.AVAILABLE.value)
        )
        total_available = total_result.scalar()
        
        provider_result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(Esim.status == EsimStatus.AVAILABLE.value)
            .group_by(Esim.provider)
        )
        provider_counts = {row[0]: row[1] for row in provider_result.all()}
        
        issued_today_result = await session.execute(
            select(func.count(Esim.id))
            .where(
                Esim.issued_to_user_id != None,
                func.date(Esim.issued_at) == today
            )
        )
        total_issued_today = issued_today_result.scalar()
        
        issued_by_provider_result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(
                Esim.issued_to_user_id != None,
                func.date(Esim.issued_at) == today
            )
            .group_by(Esim.provider)
        )
        issued_provider_counts = {row[0]: row[1] for row in issued_by_provider_result.all()}
    
    formatted_date = today.strftime("%d.%m.%y")
    
    text = "<b>📊 Сводка eSIM</b>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += f"🟢 В наличии: <b>{total_available}</b> шт.\n"
    text += format_provider_list(provider_counts)
    text += f"\n\n📈 Выдано ({formatted_date}): <b>{total_issued_today}</b> шт.\n"
    text += format_provider_list(issued_provider_counts)
    text += "\n━━━━━━━━━━━━━━━━━━"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_stats_keyboard())


@router.message(UploadState.waiting_for_photo, F.media_group_id, F.photo, AllowedUserFilter())
async def process_album(message: Message, state: FSMContext, bot: Bot):
    media_group_id = message.media_group_id
    state_data = await state.get_data()
    is_test = state_data.get("is_test", False)
    supplier = state_data.get("supplier", "")

    if media_group_id not in _album_data:
        _album_data[media_group_id] = {
            "messages": [],
            "is_test": is_test,
            "supplier": supplier,
        }

    _album_data[media_group_id]["messages"].append(message)

    await asyncio.sleep(2)

    album = _album_data.pop(media_group_id, None)
    if not album:
        return

    messages = album["messages"]
    total = len(messages)
    success_count = 0
    errors = []

    for i, msg in enumerate(messages, 1):
        try:
            file_id = msg.document.file_id if msg.document else msg.photo[-1].file_id
            file_name = msg.document.file_name if msg.document else None
            print(f"[DEBUG] Album photo {i}/{total} — file_id: {file_id}")

            print(f"[DEBUG] Downloading {file_id}...")
            file = await bot.get_file(file_id)
            photo_bytes = await bot.download_file(file.file_path)
            print(f"[DEBUG] Download complete for {file_id}")

            print(f"[DEBUG] Decoding QR for {file_id}...")
            lpa_string = await read_qr_code(photo_bytes.read())
            print(f"[DEBUG] Decode result for {file_id}: {'found' if lpa_string else 'None'}")

            if not lpa_string:
                label = f" ({file_name})" if file_name else ""
                errors.append(f"- Фото {i}{label}: QR не считался")
                continue

            if not lpa_string.startswith("LPA:1$"):
                errors.append(f"- Фото {i}: Неверный формат ({lpa_string[:30]}...)")
                continue

            async with async_session_maker() as session:
                existing = await session.execute(
                    select(Esim).where(Esim.lpa_string == lpa_string)
                )
                if existing.scalar_one_or_none():
                    errors.append(f"- Фото {i}: Дубликат ({lpa_string[:30]}...)")
                    continue

            detected_provider = get_provider_from_domain(lpa_string)
            if not detected_provider:
                errors.append(f"- Фото {i}: Не удалось определить оператора")
                continue

            async with async_session_maker() as session:
                try:
                    esim = Esim(
                        lpa_string=lpa_string,
                        provider=detected_provider,
                        image_file_id=file_id,
                        status=EsimStatus.AVAILABLE.value,
                        is_test=is_test,
                        supplier=supplier
                    )
                    session.add(esim)
                    await session.commit()
                    await session.refresh(esim)
                    esim_id = esim.id
                    success_count += 1

                    if config.google_sheet_id:
                        asyncio.create_task(google_sheets.append_new_esim(esim_id, detected_provider, lpa_string, supplier))

                    if is_test:
                        asyncio.create_task(broadcast_test_esim(detected_provider, bot))

                except Exception:
                    errors.append(f"- Фото {i}: Ошибка при сохранении в БД")

        except Exception as e:
            print(f"[CRITICAL ERROR] Album photo {i}: {e}")
            errors.append(f"- Фото {i}: Ошибка при обработке файла")

    text = f"📊 <b>Массовая загрузка завершена</b>\n\n"
    text += f"Всего обработано: {total}\n"
    text += f"✅ Успешно добавлено: {success_count}\n"
    text += f"❌ Ошибок: {len(errors)}\n"

    if errors:
        text += "\n<i>Список ошибок:</i>\n"
        text += "\n".join(errors)

    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(UploadState.waiting_for_photo, F.photo | F.document, AllowedUserFilter())
async def process_photo(message: Message, state: FSMContext, bot: Bot):
    state_data = await state.get_data()
    is_test = state_data.get("is_test", False)
    supplier = state_data.get("supplier", "")
    
    if message.photo:
        file_id = message.photo[-1].file_id
        file_name = None
    elif message.document and message.document.mime_type.startswith("image/"):
        file_id = message.document.file_id
        file_name = message.document.file_name
    else:
        await message.answer("❌ Пожалуйста, отправьте QR-код как фото или картинку (JPEG/PNG).")
        return
    
    file = await bot.get_file(file_id)
    photo_bytes = await bot.download_file(file.file_path)
    lpa_string = await read_qr_code(photo_bytes.read())
    
    if not lpa_string:
        if file_name:
            await message.answer(f"❌ В файле «{file_name}» не найден QR-код.")
        else:
            await message.answer("❌ Не удалось распознать QR-код. Попробуйте ещё раз.")
        return
    
    async with async_session_maker() as session:
        existing = await session.execute(
            select(Esim).where(Esim.lpa_string == lpa_string)
        )
        if existing.scalar_one_or_none():
            if file_name:
                await message.answer(f"❌ Файл «{file_name}» уже есть в базе!\nОтправьте следующий QR-код.")
            else:
                await message.answer("❌ Эта eSIM уже есть в базе!\nОтправьте следующий QR-код.")
            return
    
    detected_provider = get_provider_from_domain(lpa_string)
    
    if detected_provider:
        async with async_session_maker() as session:
            try:
                esim = Esim(
                    lpa_string=lpa_string,
                    provider=detected_provider,
                    image_file_id=file_id,
                    status=EsimStatus.AVAILABLE.value,
                    is_test=is_test,
                    supplier=supplier
                )
                session.add(esim)
                await session.commit()
                await session.refresh(esim)
                esim_id = esim.id
            except Exception:
                if file_name:
                    await message.answer(f"❌ Файл «{file_name}» уже есть в базе!\nОтправьте следующий QR-код или нажмите «Отмена» для выхода.")
                else:
                    await message.answer("❌ Эта eSIM уже есть в базе!\nОтправьте следующий QR-код или нажмите «Отмена» для выхода.")
                return
        
        if config.google_sheet_id:
            asyncio.create_task(google_sheets.append_new_esim(esim_id, detected_provider, lpa_string, supplier))
        
        if is_test:
            await broadcast_test_esim(detected_provider, bot)
        
        await message.answer(f"✅ eSIM ({detected_provider}) успешно добавлена! Отправьте следующее фото QR-кода или нажмите 'Отмена' для выхода.")
    else:
        await state.update_data(lpa_string=lpa_string, file_id=file_id)
        await message.answer("Выберите оператора:", reply_markup=get_provider_buttons())
        await state.set_state(UploadState.waiting_for_provider)


@router.callback_query(UploadState.waiting_for_provider, AllowedUserFilter())
async def process_provider(callback: CallbackQuery, state: FSMContext, bot: Bot):
    provider = callback.data.replace("provider:", "")
    data = await state.get_data()
    is_test = data.get("is_test", False)
    supplier = data.get("supplier", "")
    
    async with async_session_maker() as session:
        existing = await session.execute(
            select(Esim).where(Esim.lpa_string == data["lpa_string"])
        )
        if existing.scalar_one_or_none():
            try:
                await callback.message.answer("❌ Эта eSIM уже есть в базе!")
                await callback.answer()
            except Exception:
                pass
            await state.set_state(UploadState.waiting_for_photo)
            return
        
        try:
            esim = Esim(
                lpa_string=data["lpa_string"],
                provider=provider,
                image_file_id=data["file_id"],
                status=EsimStatus.AVAILABLE.value,
                is_test=is_test,
                supplier=supplier
            )
            session.add(esim)
            await session.commit()
            await session.refresh(esim)
            esim_id = esim.id
        except Exception:
            try:
                await callback.message.answer("❌ Эта eSIM уже есть в базе!")
                await callback.answer()
            except Exception:
                pass
            await state.set_state(UploadState.waiting_for_photo)
            return
    
    if config.google_sheet_id:
        asyncio.create_task(google_sheets.append_new_esim(esim_id, provider, data["lpa_string"], supplier))
    
    if is_test:
        await broadcast_test_esim(provider, bot)
    
    try:
        await callback.message.answer(f"✅ eSIM ({provider}) успешно добавлена! Отправьте следующее фото QR-кода или нажмите 'Отмена' для выхода.")
        await callback.answer()
    except Exception:
        pass
    await state.set_state(UploadState.waiting_for_photo)


@router.message(Command("cancel"), AllowedUserFilter())
@router.message(F.text == "Отмена", AllowedUserFilter())
async def cancel_upload(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    
    is_admin = message.from_user.id in config.allowed_users
    await state.clear()
    await message.answer("Загрузка отменена.", reply_markup=get_main_menu(is_admin))


async def get_stock_by_provider(is_test: bool = False) -> dict:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(
                Esim.status == EsimStatus.AVAILABLE.value,
                Esim.is_test == is_test
            )
            .group_by(Esim.provider)
        )
        return {row[0]: row[1] for row in result.all()}


@router.callback_query(F.data.startswith("get_provider:"), AllowedUserFilter())
async def process_get_esim(callback: CallbackQuery):
    provider = callback.data.replace("get_provider:", "")
    user_id = callback.from_user.id
    username = callback.from_user.username
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim)
            .where(
                Esim.provider == provider,
                Esim.status == EsimStatus.AVAILABLE.value,
                Esim.is_test == False
            )
            .order_by(Esim.created_at)
            .limit(1)
            .with_for_update()
        )
        esim = result.scalar_one_or_none()
        
        if not esim:
            await callback.message.answer("Нет доступных eSIM этого оператора.")
            try:
                await callback.answer()
            except Exception:
                pass
            return
        
        esim_id = esim.id
        esim.status = EsimStatus.ISSUED.value
        esim.issued_to_user_id = user_id
        esim.issued_at = datetime.utcnow()
        esim.updated_at = datetime.utcnow()
        await session.commit()
        
        supplier = esim.supplier if esim.supplier else config.our_supplier_name
        
        if config.google_sheet_id:
            user_identifier = f"@{username} ({user_id})" if username else str(user_id)
            formatted_date = datetime.now().strftime("%d.%m.%Y %H:%M")
            asyncio.create_task(google_sheets.update_esim_status(
                esim_id, "🔴 Issued", user_identifier, formatted_date
            ))
        
        caption = f"📱 Оператор: <b>{esim.provider}</b>\n🔗 LPA: <code>{esim.lpa_string}</code>\n📦 Поставщик: {supplier}"
        
        try:
            await callback.message.answer_photo(
                photo=esim.image_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=get_issued_esim_keyboard(esim.id)
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"{caption}\n\n<i>(⚠️ Фото QR-кода недоступно, скопируйте текстовый адрес выше)</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_issued_esim_keyboard(esim.id)
            )
    
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data == "get_test_providers", AllowedUserFilter())
async def get_test_providers(callback: CallbackQuery):
    test_stock = await get_stock_by_provider(is_test=True)
    
    buttons = []
    for provider, count in test_stock.items():
        if count > 0:
            buttons.append([InlineKeyboardButton(text=f"{provider} ({count} шт.)", callback_data=f"get_test_provider:{provider}")])
    
    if not buttons:
        await callback.message.answer("Нет доступных тестовых eSIM.")
        try:
            await callback.answer()
        except Exception:
            pass
        return
    
    try:
        await callback.message.edit_text("Выберите оператора (тестовые):", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception:
        await callback.message.answer("Выберите оператора (тестовые):", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("get_test_provider:"), AllowedUserFilter())
async def process_get_test_esim(callback: CallbackQuery, state: FSMContext):
    provider = callback.data.replace("get_test_provider:", "")
    user_id = callback.from_user.id
    username = callback.from_user.username
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim)
            .where(
                Esim.provider == provider,
                Esim.status == EsimStatus.AVAILABLE.value,
                Esim.is_test == True
            )
            .order_by(Esim.created_at)
            .limit(1)
            .with_for_update()
        )
        esim = result.scalar_one_or_none()
        
        if not esim:
            await callback.message.answer("Нет доступных тестовых eSIM этого оператора.")
            try:
                await callback.answer()
            except Exception:
                pass
            return
        
        esim_id = esim.id
        supplier = esim.supplier if esim.supplier else config.our_supplier_name
        image_file_id = esim.image_file_id
        lpa_string = esim.lpa_string
        
        esim.status = EsimStatus.ISSUED.value
        esim.issued_to_user_id = user_id
        esim.issued_at = datetime.utcnow()
        esim.updated_at = datetime.utcnow()
        await session.commit()
        
        if config.google_sheet_id:
            user_identifier = f"@{username} ({user_id})" if username else str(user_id)
            formatted_date = datetime.now().strftime("%d.%m.%Y %H:%M")
            asyncio.create_task(google_sheets.update_esim_status(
                esim_id, "🔴 Issued", user_identifier, formatted_date
            ))
        
        caption = f"📱 Оператор: <b>{provider}</b> (ТЕСТОВАЯ)\n🔗 LPA: <code>{lpa_string}</code>\n📦 Поставщик: {supplier}"
        
        try:
            await callback.message.answer_photo(
                photo=image_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=get_issued_esim_keyboard(esim_id)
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"{caption}\n\n<i>(⚠️ Фото QR-кода недоступно, скопируйте текстовый адрес выше)</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_issued_esim_keyboard(esim_id)
            )
        
        await state.update_data(esim_id=esim_id)
        await state.set_state(SipAttachState.waiting_for_sip_number)
        await callback.message.answer("Введите номер сипа:")
        
        try:
            await callback.answer()
        except Exception:
            pass
    
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("attach_sip:"), AllowedUserFilter())
async def process_attach_sip(callback: CallbackQuery, state: FSMContext):
    esim_id = int(callback.data.replace("attach_sip:", ""))
    
    await state.update_data(esim_id=esim_id)
    await state.set_state(SipAttachState.waiting_for_sip_number)
    
    await callback.message.answer("Введите номер сипа:")
    
    try:
        await callback.answer()
    except Exception:
        pass


@router.message(SipAttachState.waiting_for_sip_number, AllowedUserFilter())
async def process_sip_number(message: Message, state: FSMContext, bot: Bot):
    sip_number = message.text.strip()
    
    if not sip_number.isdigit():
        await message.answer("❌ Ошибка: Номер сипа должен состоять только из цифр. Пожалуйста, введите корректный номер:")
        return
    
    state_data = await state.get_data()
    esim_id = state_data.get("esim_id")
    
    if not esim_id:
        await message.answer("Ошибка: данные сессии потеряны. Начните заново.")
        await state.clear()
        return
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim).where(Esim.id == esim_id)
        )
        esim = result.scalar_one_or_none()
    
    if not esim:
        await message.answer("Ошибка: eSIM не найдена.")
        await state.clear()
        return
    
    section_name = "Test" if esim.is_test else "Main"
    supplier = esim.supplier if esim.supplier else config.our_supplier_name
    
    notify_chat_id = config.test_slot_notify_chat_id
    
    if notify_chat_id:
        notify_text = (
            f"⭕️ <b>{sip_number}</b> слот ({esim.provider}) - {section_name}\n\n"
            f"📦 Поставщик: {supplier}\n\n"
            f"🆔 ID симки: {esim_id}"
        )
        try:
            chat_id = int(notify_chat_id)
            qa_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Work", callback_data=f"qa_work:{esim_id}:{sip_number}")],
                [InlineKeyboardButton(text="⛔️ Instant Block", callback_data=f"qa_instant:{esim_id}:{sip_number}")]
            ])
            await bot.send_message(chat_id=chat_id, text=notify_text, parse_mode=ParseMode.HTML, reply_markup=qa_keyboard)
        except Exception as e:
            print(f"Error sending SIP notification: {e}")
    
    is_admin = message.from_user.id in config.allowed_users
    await message.answer("Сип привязан. Уведомление отправлено.", reply_markup=get_main_menu(is_admin))
    await state.clear()


@router.callback_query(F.data.startswith("to_reserve:"), AllowedUserFilter())
async def process_to_reserve(callback: CallbackQuery):
    esim_id = int(callback.data.replace("to_reserve:", ""))
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim).where(Esim.id == esim_id)
        )
        esim = result.scalar_one_or_none()
        
        if not esim:
            try:
                await callback.answer("eSIM не найдена.", show_alert=True)
            except Exception:
                pass
            return
        
        esim.in_reserve = True
        await session.commit()
    
    if config.google_sheet_id:
        username = callback.from_user.username
        user_identifier = f"@{username} ({callback.from_user.id})" if username else str(callback.from_user.id)
        formatted_date = datetime.now().strftime("%d.%m.%Y %H:%M")
        asyncio.create_task(google_sheets.update_esim_status(
            esim_id, "🟡 Reserved", user_identifier, formatted_date
        ))
    
    try:
        await callback.message.edit_text(
            f"✅ eSIM #{esim_id} переведена в Резерв.",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
    except TelegramBadRequest:
        try:
            await callback.message.edit_caption(
                caption=f"✅ eSIM #{esim_id} переведена в Резерв.",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
        except Exception:
            pass
    except Exception:
        pass
    
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data == "menu_reserve_sync", AllowedUserFilter())
async def reserve_sync_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(func.count(Esim.id)).where(Esim.in_reserve == True)
        )
        current_count = result.scalar()
    
    await state.update_data(current_reserve_count=current_count)
    await state.set_state(ReserveSyncState.waiting_for_reserve_count)
    
    await callback.message.answer(
        f"Сейчас в резерве числится <b>{current_count}</b> симок.\n"
        f"Введите актуальное количество болванок, которые остались у вас на руках (чтобы списать потраченные):",
        parse_mode=ParseMode.HTML
    )
    
    try:
        await callback.answer()
    except Exception:
        pass


@router.message(ReserveSyncState.waiting_for_reserve_count, AllowedUserFilter())
async def process_reserve_sync(message: Message, state: FSMContext):
    user_input = message.text.strip()
    
    if not user_input.isdigit():
        await message.answer("❌ Пожалуйста, введите число.")
        return
    
    new_count = int(user_input)
    
    state_data = await state.get_data()
    current_count = state_data.get("current_reserve_count", 0)
    
    if new_count > current_count:
        await message.answer(f"Ошибка: Вы не можете указать больше, чем числится в базе ({current_count}).")
        await state.clear()
        is_admin = message.from_user.id in config.allowed_users
        await message.answer("⚙️ Меню:", reply_markup=get_main_menu(is_admin))
        return
    
    to_remove = current_count - new_count
    
    if to_remove > 0:
        async with async_session_maker() as session:
            result = await session.execute(
                select(Esim)
                .where(Esim.in_reserve == True)
                .order_by(Esim.updated_at.asc())
                .limit(to_remove)
            )
            esims_to_remove = result.scalars().all()
            
            removed_ids = []
            for esim in esims_to_remove:
                esim.in_reserve = False
                removed_ids.append(esim.id)
            
            await session.commit()
        
        if config.google_sheet_id:
            for esim_id in removed_ids:
                try:
                    asyncio.create_task(google_sheets.update_esim_status_only(
                        esim_id, "🔴 Issued"
                    ))
                except Exception as e:
                    print(f"[ERROR] Failed to queue GS update for esim_id {esim_id}: {e}")
    
    await message.answer(
        f"✅ Успешно! Списано <b>{to_remove}</b> симок. Текущий остаток в резерве: <b>{new_count}</b>.",
        parse_mode=ParseMode.HTML
    )
    await state.clear()
    is_admin = message.from_user.id in config.allowed_users
    await message.answer("⚙️ Меню:", reply_markup=get_main_menu(is_admin))


@router.callback_query(F.data == "test_esim_dead", AllowedUserFilter())
async def process_test_esim_dead(callback: CallbackQuery, state: FSMContext, bot: Bot):
    state_data = await state.get_data()
    
    esim_id = state_data.get("test_esim_id")
    provider = state_data.get("test_provider")
    lpa_string = state_data.get("test_lpa_string")
    image_file_id = state_data.get("test_image_file_id")
    supplier = state_data.get("test_supplier")
    user_id = state_data.get("test_user_id")
    username = state_data.get("test_username")
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim).where(Esim.id == esim_id)
        )
        esim = result.scalar_one_or_none()
        
        if not esim:
            await callback.message.answer("Ошибка: eSIM не найдена.")
            await state.clear()
            return
        
        esim.status = EsimStatus.ISSUED.value
        esim.issued_to_user_id = user_id
        esim.issued_at = datetime.utcnow()
        esim.updated_at = datetime.utcnow()
        await session.commit()
        
        if config.google_sheet_id:
            user_identifier = f"@{username} ({user_id})" if username else str(user_id)
            formatted_date = datetime.now().strftime("%d.%m.%Y %H:%M")
            asyncio.create_task(google_sheets.update_esim_status(
                esim_id, "🔴 Issued", user_identifier, formatted_date
            ))
    
    if config.dead_sim_notify_chat_id:
        caption = f"❌ НЕРАБОЧАЯ СИМКА ❌\nВозвращена пользователем: @{username}\n\n📱 Оператор: <b>{provider}</b>\n🔗 LPA: <code>{lpa_string}</code>\n📦 Поставщик: {supplier}"
        try:
            await bot.send_photo(
                chat_id=int(config.dead_sim_notify_chat_id),
                photo=image_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
        except TelegramBadRequest:
            await bot.send_message(
                chat_id=int(config.dead_sim_notify_chat_id),
                text=f"{caption}\n\n<i>(Фото недоступно)</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    
    await callback.message.edit_text(
        "Симка отмечена как нерабочая и отправлена администраторам. Вы можете взять другую.",
        reply_markup=None
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data.startswith("return:"), AllowedUserFilter())
async def return_esim(callback: CallbackQuery):
    esim_id = int(callback.data.replace("return:", ""))
    user_id = callback.from_user.id
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim).where(Esim.id == esim_id)
        )
        esim = result.scalar_one_or_none()
        
        if not esim:
            try:
                await callback.answer("eSIM не найдена.", show_alert=True)
            except Exception:
                pass
            return
        
        if esim.issued_to_user_id != user_id:
            await callback.answer("Вы не можете вернуть эту eSIM.", show_alert=True)
            return

        esim.status = EsimStatus.AVAILABLE.value
        esim.issued_to_user_id = None
        esim.updated_at = datetime.utcnow()
        await session.commit()
        
        if config.google_sheet_id:
            asyncio.create_task(google_sheets.update_esim_status(
                esim_id, "🟢 Available", "", ""
            ))
        
        await callback.message.answer("✅ eSIM возвращена в базу.")
        await callback.message.delete()
    
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("dead_esim:"), AllowedUserFilter())
async def process_dead_esim(callback: CallbackQuery, state: FSMContext, bot: Bot):
    esim_id = int(callback.data.replace("dead_esim:", ""))
    user_id = callback.from_user.id
    username = callback.from_user.username
    
    esim_provider = None
    esim_lpa = None
    esim_image = None
    esim_supplier = None
    esim_is_test = False
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim).where(Esim.id == esim_id)
        )
        esim = result.scalar_one_or_none()
        
        if not esim:
            try:
                await callback.answer("eSIM не найдена.", show_alert=True)
            except Exception:
                pass
            return
        
        if esim.issued_to_user_id != user_id:
            await callback.answer("Вы не можете пометить эту eSIM.", show_alert=True)
            return

        esim_provider = esim.provider
        esim_lpa = esim.lpa_string
        esim_image = esim.image_file_id
        esim_supplier = esim.supplier
        esim_is_test = esim.is_test
        
        esim.status = EsimStatus.ISSUED.value
        esim.updated_at = datetime.utcnow()
        await session.commit()
        
        if config.google_sheet_id:
            user_identifier = f"@{username}" if username else str(user_id)
            formatted_date = datetime.now().strftime("%d.%m.%Y %H:%M")
            asyncio.create_task(google_sheets.update_esim_status(
                esim_id, "❌ Invalid", user_identifier, formatted_date
            ))
    
    if config.dead_sim_notify_chat_id and esim_provider:
        caption = f"❌ НЕРАБОЧАЯ СИМКА ❌\nВозвращена пользователем: @{username}\n\n📱 Оператор: <b>{esim_provider}</b>\n🔗 LPA: <code>{esim_lpa}</code>\n📦 Поставщик: {esim_supplier or config.our_supplier_name}\n\n🆔 ID симки: {esim_id}"
        try:
            chat_id = int(config.dead_sim_notify_chat_id)
            await bot.send_photo(
                chat_id=chat_id,
                photo=esim_image,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
        except TelegramBadRequest:
            await bot.send_message(
                chat_id=int(config.dead_sim_notify_chat_id),
                text=f"{caption}\n\n<i>(Фото недоступно)</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    
    await state.clear()
    
    new_caption = f"❌ Не ворк\n\n👌 Подтверждено: @{username}"
    try:
        await callback.message.edit_caption(
            caption=new_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
    except TelegramBadRequest:
        try:
            await callback.message.edit_text(
                text=f"{callback.message.text}\n\n(Отмечена как нерабочая)" if callback.message.text else new_caption,
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
        except Exception:
            pass
    except Exception:
        pass
    
    try:
        await callback.answer()
    except Exception:
        pass


def get_admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="admin_add_user")],
        [InlineKeyboardButton(text="➖ Удалить пользователя", callback_data="admin_remove_user")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_list_users")],
    ])


@router.message(F.text == "⚙️ Админ-панель", AllowedUserFilter())
async def admin_panel(message: Message):
    is_admin = message.from_user.id in config.allowed_users
    if not is_admin:
        await message.answer("⛔️ У вас нет доступа к админ-панели.")
        return
    
    await message.answer("⚙️ Админ-панель:", reply_markup=get_admin_panel_keyboard())


@router.callback_query(F.data == "admin_list_users", AllowedUserFilter())
async def admin_list_users(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await state.clear()
    is_admin = callback.from_user.id in config.allowed_users
    if not is_admin:
        try:
            await callback.answer("⛔️ У вас нет доступа.", show_alert=True)
        except Exception:
            pass
        return
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).order_by(User.added_at.desc()).limit(20)
        )
        users = result.scalars().all()
    
    if not users:
        text = "👥 Список пользователей с доступом:\n(пусто)"
    else:
        text = "👥 Список пользователей с доступом:\n"
        for i, user in enumerate(users, 1):
            try:
                chat = await bot.get_chat(user.telegram_id)
                username = f"@{chat.username}" if chat.username else "без username"
            except Exception:
                username = "user not found"
            text += f"{i}. ID: {user.telegram_id} ({username})\n"
    
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_panel_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=get_admin_panel_keyboard())
    
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data == "admin_add_user", AllowedUserFilter())
async def admin_add_user_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_admin = callback.from_user.id in config.allowed_users
    if not is_admin:
        try:
            await callback.answer("⛔️ У вас нет доступа.", show_alert=True)
        except Exception:
            pass
        return
    
    await callback.message.answer("Введите Telegram ID пользователя, которому нужно выдать доступ:")
    await state.set_state(AdminState.waiting_for_add_user_id)
    try:
        await callback.answer()
    except Exception:
        pass


@router.message(AdminState.waiting_for_add_user_id, AllowedUserFilter())
async def admin_add_user_process(message: Message, state: FSMContext):
    is_admin = message.from_user.id in config.allowed_users
    if not is_admin:
        await state.clear()
        await message.answer("⛔️ У вас нет доступа.")
        return
    
    user_id_str = message.text.strip()
    
    if not user_id_str.isdigit():
        await message.answer("❌ Некорректный ID. Введите числовой Telegram ID.")
        return
    
    user_id = int(user_id_str)
    
    if user_id in config.allowed_users:
        await message.answer("⚠️ Этот пользователь уже является супер-админом (в ALLOWED_USERS).")
        await state.clear()
        await message.answer("⚙️ Админ-панель:", reply_markup=get_admin_panel_keyboard())
        return
    
    async with async_session_maker() as session:
        existing = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
        if existing.scalar_one_or_none():
            await message.answer("❌ Этот пользователь уже есть в базе.")
            await state.clear()
            return
        
        username = message.from_user.username if message.from_user else None
        new_user = User(telegram_id=user_id, username=username)
        session.add(new_user)
        await session.commit()
    
    await message.answer(f"✅ Пользователь {user_id} успешно добавлен!")
    await state.clear()
    await message.answer("⚙️ Админ-панель:", reply_markup=get_admin_panel_keyboard())


@router.callback_query(F.data == "admin_remove_user", AllowedUserFilter())
async def admin_remove_user_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_admin = callback.from_user.id in config.allowed_users
    if not is_admin:
        try:
            await callback.answer("⛔️ У вас нет доступа.", show_alert=True)
        except Exception:
            pass
        return
    
    await callback.message.answer("Введите Telegram ID пользователя, которого нужно удалить:")
    await state.set_state(AdminState.waiting_for_remove_user_id)
    try:
        await callback.answer()
    except Exception:
        pass


@router.message(AdminState.waiting_for_remove_user_id, AllowedUserFilter())
async def admin_remove_user_process(message: Message, state: FSMContext):
    is_admin = message.from_user.id in config.allowed_users
    if not is_admin:
        await state.clear()
        await message.answer("⛔️ У вас нет доступа.")
        return
    
    user_id_str = message.text.strip()
    
    if not user_id_str.isdigit():
        await message.answer("❌ Некорректный ID. Введите числовой Telegram ID.")
        return
    
    user_id = int(user_id_str)
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            await message.answer("❌ Пользователь не найден в базе.")
            await state.clear()
            return
        
        await session.delete(user)
        await session.commit()
    
    await message.answer(f"✅ Пользователь {user_id} удален. У него больше нет доступа.")
    await state.clear()
    await message.answer("⚙️ Админ-панель:", reply_markup=get_admin_panel_keyboard())


@router.callback_query(F.data == "reset_stats", AllowedUserFilter())
async def reset_stats_confirm(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите сбросить статистику за сегодня? Это действие обнулит счетчик выданных eSIM.",
        reply_markup=get_reset_confirmation_keyboard()
    )
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data == "confirm_reset_stats", AllowedUserFilter())
async def confirm_reset_stats(callback: CallbackQuery):
    today = date.today()
    
    async with async_session_maker() as session:
        from datetime import timedelta
        yesterday = today - timedelta(days=1)
        
        await session.execute(
            update(Esim)
            .where(
                Esim.issued_to_user_id != None,
                func.date(Esim.issued_at) == today
            )
            .values(issued_at=yesterday)
        )
        await session.commit()
    
    try:
        await callback.answer()
    except Exception:
        pass


# Global fallback handler - MUST be at the very bottom so other handlers run first
MENU_BUTTONS = [
    "📥 Загрузить eSIM", "🧪 Загрузить тестовую",
    "📤 Получить eSIM", "📊 Статистика",
    "⚙️ Админ-панель", "Отмена"
]

@router.message(F.text, AllowedUserFilter())
async def handle_unknown_text(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None and message.text not in MENU_BUTTONS:
        is_admin = message.from_user.id in config.allowed_users
        await message.answer(
            "Пожалуйста, используйте кнопки меню.",
            reply_markup=get_main_menu(is_admin)
        )
    
    today = date.today()
    
    async with async_session_maker() as session:
        total_result = await session.execute(
            select(func.count(Esim.id)).where(Esim.status == EsimStatus.AVAILABLE.value)
        )
        total_available = total_result.scalar()
        
        provider_result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(Esim.status == EsimStatus.AVAILABLE.value)
            .group_by(Esim.provider)
        )
        provider_counts = {row[0]: row[1] for row in provider_result.all()}
        
        issued_today_result = await session.execute(
            select(func.count(Esim.id))
            .where(
                Esim.issued_to_user_id != None,
                func.date(Esim.issued_at) == today
            )
        )
        total_issued_today = issued_today_result.scalar()
        
        issued_by_provider_result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(
                Esim.issued_to_user_id != None,
                func.date(Esim.issued_at) == today
            )
            .group_by(Esim.provider)
        )
        issued_provider_counts = {row[0]: row[1] for row in issued_by_provider_result.all()}
    
    formatted_date = today.strftime("%d.%m.%y")
    
    text = "<b>📊 Сводка eSIM</b>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += f"🟢 В наличии: <b>{total_available}</b> шт.\n"
    text += format_provider_list(provider_counts)
    text += f"\n\n📈 Выдано ({formatted_date}): <b>{total_issued_today}</b> шт.\n"
    text += format_provider_list(issued_provider_counts)
    text += "\n━━━━━━━━━━━━━━━━━━"
    
    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_stats_keyboard())
    except Exception:
        pass



@router.callback_query(F.data == "cancel_reset_stats", AllowedUserFilter())
async def cancel_reset_stats(callback: CallbackQuery):
    today = date.today()
    
    async with async_session_maker() as session:
        total_result = await session.execute(
            select(func.count(Esim.id)).where(Esim.status == EsimStatus.AVAILABLE.value)
        )
        total_available = total_result.scalar()
        
        provider_result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(Esim.status == EsimStatus.AVAILABLE.value)
            .group_by(Esim.provider)
        )
        provider_counts = {row[0]: row[1] for row in provider_result.all()}
        
        issued_today_result = await session.execute(
            select(func.count(Esim.id))
            .where(
                Esim.issued_to_user_id != None,
                func.date(Esim.issued_at) == today
            )
        )
        total_issued_today = issued_today_result.scalar()
        
        issued_by_provider_result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(
                Esim.issued_to_user_id != None,
                func.date(Esim.issued_at) == today
            )
            .group_by(Esim.provider)
        )
        issued_provider_counts = {row[0]: row[1] for row in issued_by_provider_result.all()}
    
    formatted_date = today.strftime("%d.%m.%y")
    
    text = "<b>📊 Сводка eSIM</b>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += f"🟢 В наличии: <b>{total_available}</b> шт.\n"
    text += format_provider_list(provider_counts)
    text += f"\n\n📈 Выдано ({formatted_date}): <b>{total_issued_today}</b> шт.\n"
    text += format_provider_list(issued_provider_counts)
    text += "\n━━━━━━━━━━━━━━━━━━"
    
    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_stats_keyboard())
    except Exception:
        pass
    
    try:
        await callback.answer()
    except Exception:
        pass


import re

@group_router.callback_query(F.data.startswith("qa_work:"))
async def process_qa_work(callback: CallbackQuery, bot: Bot):
    print(f"QA Work button clicked! callback.data: {callback.data}")
    parts = callback.data.replace("qa_work:", "").split(":")
    esim_id = int(parts[0])
    slot_number = parts[1] if len(parts) > 1 else "?"
    username = callback.from_user.username or "Unknown"
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim).where(Esim.id == esim_id)
        )
        esim = result.scalar_one_or_none()
    
    if esim:
        operator = esim.provider
        section_name = "Test" if esim.is_test else "Main"
        supplier = esim.supplier if esim.supplier else None
    else:
        operator = "?"
        section_name = "Main"
        supplier = None
    
    supplier_line = f"📦 Поставщик: {supplier}" if supplier else "📦 Поставщик: не указан"
    
    new_text = f"✅ <b>{slot_number}</b> слот ({operator}) - {section_name}\n\n{supplier_line}\n\n👌 Подтверждено: @{username}"
    
    try:
        await callback.message.edit_text(
            new_text,
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
    except Exception as e:
        print(f"Error editing QA message: {e}")
    
    try:
        await callback.answer()
    except Exception:
        pass


@group_router.callback_query(F.data.startswith("qa_instant:"))
async def process_qa_instant(callback: CallbackQuery, bot: Bot):
    print(f"QA Instant button clicked! callback.data: {callback.data}")
    parts = callback.data.replace("qa_instant:", "").split(":")
    esim_id = int(parts[0])
    slot_number = parts[1] if len(parts) > 1 else "?"
    username = callback.from_user.username or "Unknown"
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(Esim).where(Esim.id == esim_id)
        )
        esim = result.scalar_one_or_none()
        
        if esim:
            esim.status = "instant_blocked"
            esim.updated_at = datetime.utcnow()
            await session.commit()
            
            if config.google_sheet_id:
                asyncio.create_task(google_sheets.update_esim_status(
                    esim_id, "⛔️ Instant", f"@{username}", datetime.now().strftime("%d.%m.%Y %H:%M")
                ))
    
    if esim:
        operator = esim.provider
        supplier = esim.supplier if esim.supplier else config.our_supplier_name
        section_name = "Test" if esim.is_test else "Main"
        lpa_string = esim.lpa_string if esim.lpa_string else None
    else:
        operator = "?"
        supplier = config.our_supplier_name
        section_name = "Main"
        lpa_string = None
    
    lpa_line = f"📡 LPA: <code>{lpa_string}</code>" if lpa_string else "📡 LPA: <i>не указана</i>"
    
    new_text = f"⛔️ Instant Block: <b>{slot_number}</b> слот ({operator}) - {section_name}\n\n📦 Поставщик: {supplier}\n\n🆔 ID симки: {esim_id}\n\n{lpa_line}\n\n👌 Подтверждено: @{username}"
    
    try:
        await callback.message.edit_text(
            new_text,
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
    except Exception as e:
        print(f"Error editing QA message: {e}")
    
    try:
        await callback.answer()
    except Exception:
        pass

