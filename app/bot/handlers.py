from datetime import datetime, date
from aiogram import Router, F, Bot
from aiogram.types import Message, PhotoSize, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Esim, EsimStatus, User
from app.db.database import async_session_maker
from app.utils.qr_reader import read_qr_code
from app.utils.config import config
from app.bot.filters import AllowedUserFilter

router = Router()
storage = MemoryStorage()


class UploadState(StatesGroup):
    waiting_for_photo = State()
    waiting_for_provider = State()


class GetEsimState(StatesGroup):
    selecting_provider = State()


class AdminState(StatesGroup):
    waiting_for_add_user_id = State()
    waiting_for_remove_user_id = State()


PROVIDERS = ["МТС", "Билайн", "Мегафон", "Tele2", "Другие"]

PROVIDER_MAPPING = {
    "esim.mts.ru": "МТС",
    "mno-02.esimservices.com": "Билайн",
    "mno-0b.esimservices.com": "Йота",
    "mno-04.esimservices.com": "Мегафон",
    "smdp.alfa.edu.az": "Алфа",
    "smdp.tele2.ru": "Tele2",
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
        except Exception:
            pass


def get_main_menu(is_admin: bool = False):
    keyboard = [
        [KeyboardButton(text="📥 Загрузить eSIM"), KeyboardButton(text="🧪 Загрузить тестовую")],
        [KeyboardButton(text="📤 Получить eSIM"), KeyboardButton(text="📊 Статистика")],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=keyboard)


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
        [InlineKeyboardButton(text="🔙 Вернуть в базу", callback_data=f"return:{esim_id}")],
    ])


def get_stats_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить активность за сегодня", callback_data="reset_stats")],
    ])


def get_reset_confirmation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, обновить", callback_data="confirm_reset_stats")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel_reset_stats")],
    ])


@router.message(Command("start"), AllowedUserFilter())
async def cmd_start(message: Message):
    is_admin = message.from_user.id in config.allowed_users
    await message.answer(
        "Добро пожаловать в меню eSIM менеджера!",
        reply_markup=get_main_menu(is_admin)
    )


@router.message(F.text == "📥 Загрузить eSIM", AllowedUserFilter())
async def upload_esim_start(message: Message, state: FSMContext):
    await message.answer("Пожалуйста, отправьте фото QR-кода eSIM.")
    await state.update_data(is_test=False)
    await state.set_state(UploadState.waiting_for_photo)


@router.message(F.text == "🧪 Загрузить тестовую", AllowedUserFilter())
async def upload_test_esim_start(message: Message, state: FSMContext):
    await message.answer("Пожалуйста, отправьте фото QR-кода тестовой eSIM.")
    await state.update_data(is_test=True)
    await state.set_state(UploadState.waiting_for_photo)


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
                Esim.status == EsimStatus.ISSUED.value,
                func.date(Esim.updated_at) == today
            )
        )
        total_issued_today = issued_today_result.scalar()
        
        issued_by_provider_result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(
                Esim.status == EsimStatus.ISSUED.value,
                func.date(Esim.updated_at) == today
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


@router.message(UploadState.waiting_for_photo, F.photo | F.document, AllowedUserFilter())
async def process_photo(message: Message, state: FSMContext, bot: Bot):
    state_data = await state.get_data()
    is_test = state_data.get("is_test", False)
    
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type.startswith("image/"):
        file_id = message.document.file_id
    else:
        await message.answer("❌ Пожалуйста, отправьте QR-код как фото или картинку (JPEG/PNG).")
        return
    
    file = await bot.get_file(file_id)
    photo_bytes = await bot.download_file(file.file_path)
    lpa_string = await read_qr_code(photo_bytes.read())
    
    if not lpa_string:
        await message.answer("❌ Не удалось распознать QR-код. Попробуйте ещё раз.")
        return
    
    async with async_session_maker() as session:
        existing = await session.execute(
            select(Esim).where(Esim.lpa_string == lpa_string)
        )
        if existing.scalar_one_or_none():
            await message.answer("❌ Эта eSIM уже есть в базе! Отправьте следующее фото QR-кода.")
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
                    is_test=is_test
                )
                session.add(esim)
                await session.commit()
            except Exception:
                await message.answer("❌ Эта eSIM уже есть в базе! Отправьте следующее фото QR-кода или нажмите 'Отмена' для выхода.")
                return
        
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
                is_test=is_test
            )
            session.add(esim)
            await session.commit()
        except Exception:
            try:
                await callback.message.answer("❌ Эта eSIM уже есть в базе!")
                await callback.answer()
            except Exception:
                pass
            await state.set_state(UploadState.waiting_for_photo)
            return
    
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
        
        esim.status = EsimStatus.ISSUED.value
        esim.issued_to_user_id = user_id
        esim.updated_at = datetime.utcnow()
        await session.commit()
        
        try:
            await callback.message.answer_photo(
                photo=esim.image_file_id,
                caption=f"Оператор: <b>{esim.provider}</b>\nQR строка:\n<code>{esim.lpa_string}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_issued_esim_keyboard(esim.id)
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"Оператор: <b>{esim.provider}</b>\nQR строка:\n<code>{esim.lpa_string}</code>\n\n<i>(⚠️ Фото QR-кода недоступно, скопируйте текстовый адрес выше)</i>",
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
async def process_get_test_esim(callback: CallbackQuery):
    provider = callback.data.replace("get_test_provider:", "")
    user_id = callback.from_user.id
    
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
        
        esim.status = EsimStatus.ISSUED.value
        esim.issued_to_user_id = user_id
        esim.updated_at = datetime.utcnow()
        await session.commit()
        
        try:
            await callback.message.answer_photo(
                photo=esim.image_file_id,
                caption=f"Оператор: <b>{esim.provider}</b> (ТЕСТОВАЯ)\nQR строка:\n<code>{esim.lpa_string}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_issued_esim_keyboard(esim.id)
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"Оператор: <b>{esim.provider}</b> (ТЕСТОВАЯ)\nQR строка:\n<code>{esim.lpa_string}</code>\n\n<i>(⚠️ Фото QR-кода недоступно, скопируйте текстовый адрес выше)</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_issued_esim_keyboard(esim.id)
            )
    
    try:
        await callback.answer()
    except Exception:
        pass


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
            try:
                await callback.answer("Вы можете вернуть только свои eSIM.", show_alert=True)
            except Exception:
                pass
            return
        
        esim.status = EsimStatus.AVAILABLE.value
        esim.issued_to_user_id = None
        esim.updated_at = datetime.utcnow()
        await session.commit()
        
        await callback.message.answer("✅ eSIM возвращена в базу.")
        await callback.message.delete()
    
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
async def admin_list_users(callback: CallbackQuery):
    is_admin = callback.from_user.id in config.allowed_users
    if not is_admin:
        try:
            await callback.answer("⛔️ У вас нет доступа.", show_alert=True)
        except Exception:
            pass
        return
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).order_by(User.added_at.desc())
        )
        users = result.scalars().all()
    
    if not users:
        text = "👥 Список пользователей с доступом:\n(пусто)"
    else:
        text = "👥 Список пользователей с доступом:\n"
        for i, user in enumerate(users, 1):
            username_str = f" (@{user.username})" if user.username else ""
            text += f"{i}. ID: {user.telegram_id}{username_str}\n"
    
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
                Esim.status == EsimStatus.ISSUED.value,
                func.date(Esim.updated_at) == today
            )
            .values(updated_at=yesterday)
        )
        await session.commit()
    
    try:
        await callback.answer("✅ Статистика обновлена!", show_alert=True)
    except Exception:
        pass
    
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
                Esim.status == EsimStatus.ISSUED.value,
                func.date(Esim.updated_at) == today
            )
        )
        total_issued_today = issued_today_result.scalar()
        
        issued_by_provider_result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(
                Esim.status == EsimStatus.ISSUED.value,
                func.date(Esim.updated_at) == today
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
                Esim.status == EsimStatus.ISSUED.value,
                func.date(Esim.updated_at) == today
            )
        )
        total_issued_today = issued_today_result.scalar()
        
        issued_by_provider_result = await session.execute(
            select(Esim.provider, func.count(Esim.id))
            .where(
                Esim.status == EsimStatus.ISSUED.value,
                func.date(Esim.updated_at) == today
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

