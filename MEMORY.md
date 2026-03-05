# Project Memory & Changelog

## 1. Architecture Overview

This is a Telegram bot for eSIM management built with:
- **Framework:** aiogram 3.x (async Python)
- **Database:** PostgreSQL with SQLAlchemy 2.0 (async via asyncpg)
- **Image Processing:** OpenCV (cv2) + pyzbar for QR code recognition
- **Migrations:** Alembic

### Image Storage Strategy
The bot uses Telegram's native file storage via `file_id`. When a user uploads a QR code image, we store only the Telegram `file_id` reference, not the actual image data. This keeps the database small but means images may become unavailable if Telegram purges them.

### Database Schema

**`esims` table:**
```python
class Esim(Base):
    __tablename__ = "esims"
    
    id: int (Primary Key)
    lpa_string: str (Unique, indexed)  # LPA address like "LPA:1$smdp.domain.com$code"
    provider: str                       # Operator name: МТС, Билайн, Мегафон, Tele2, etc.
    image_file_id: str                  # Telegram file_id for the QR image
    status: str                         # 'available' or 'issued'
    issued_to_user_id: int | None       # Telegram user ID who received the eSIM
    created_at: datetime                # When added to database
    updated_at: datetime                # Auto-updated on status changes
```

**`users` table:**
```python
class User(Base):
    __tablename__ = "users"
    
    id: int (Primary Key)
    telegram_id: int (Unique, indexed)  # Telegram user ID
    username: str | None                 # Telegram username (for easier identification)
    added_at: datetime                   # When user was added
```

## 2. Key Business Logic Rules (DO NOT BREAK THESE)

### Authorization System
The bot has two types of users:
1. **Superadmins** - IDs listed in `ALLOWED_USERS` in `.env` file. They see the "⚙️ Админ-панель" button.
2. **Regular Users** - Added via Admin Panel, stored in `users` table. They only see basic menu (Upload/Get/Stats).

Both the **Middleware** and the **Filter** check authorization:
```python
# Check if superadmin (in ALLOWED_USERS)
if user_id in config.allowed_users:
    return True

# Check database for regular users
async with async_session_maker() as session:
    result = await session.execute(
        select(User).where(User.telegram_id == user_id)
    )
    user = result.scalar_one_or_none()
    return user is not None
```

### FSM Upload Loop
The upload process is **continuous** to support batch uploading:
- After successful upload, the bot stays in `UploadState.waiting_for_photo`
- Success message: "✅ eSIM ({provider}) успешно добавлена! Отправьте следующее фото QR-кода или нажмите 'Отмена' для выхода."
- Use `state.set_state(UploadState.waiting_for_photo)` to loop back, NOT `state.clear()`
- Cancel with `/cancel` command or "Отмена" text

### Photo/Document Handling
The upload handler accepts both:
- `F.photo` - Compressed Telegram photos (use `message.photo[-1].file_id`)
- `F.document` - Raw files (check `message.document.mime_type.startswith("image/")` first)

Non-image documents receive error: "❌ Пожалуйста, отправьте QR-код как фото или картинку (JPEG/PNG)."

### Provider Auto-Detection
We extract the SM-DP+ domain from the LPA string and map it to providers:
```python
PROVIDER_MAPPING = {
    "ESIM.MTS.RU": "МТС",
    "mno-02.esimservices.com": "Билайн",
    "mno-0b.esimservices.com": "Йота",
    "mno-04.esimservices.com": "Мегафон",
    "smdp.alfa.edu.az": "Алфа",
    "smdp.tele2.ru": "Tele2",
}

def get_provider_from_domain(lpa_string: str) -> str | None:
    domain = lpa_string.split("$")[1].lower()  # Extract between 1$ and next $
    return PROVIDER_MAPPING.get(domain)
```

If no match found, bot asks for manual provider selection via inline keyboard.

### Issue Mechanism (Race Condition Protection)
When issuing an eSIM, we use `SELECT ... FOR UPDATE`:
```python
result = await session.execute(
    select(Esim)
    .where(Esim.provider == provider, Esim.status == EsimStatus.AVAILABLE.value)
    .order_by(Esim.created_at)
    .limit(1)
    .with_for_update()
)
```

### Output Formatting
All issued eSIM messages use `ParseMode.HTML` with template:
```
Оператор: <b>{provider}</b>
QR строка:
<code>{lpa_string}</code>
```

**Fallback:** If `answer_photo` fails with `TelegramBadRequest` (file_id expired), send text-only message with:
```
Оператор: <b>{provider}</b>
QR строка:
<code>{lpa_string}</code>

<i>(⚠️ Фото QR-кода недоступно, скопируйте текстовый адрес выше)</i>
```

Inline keyboard "🔙 Вернуть в базу" is attached to both cases.

### Admin Panel (FSM)
Accessible only to superadmins (ALLOWED_USERS). Three functions:
1. **List Users** - Shows all users in `users` table with their IDs and usernames
2. **Add User** - FSM state `AdminState.waiting_for_add_user_id`, validates numeric ID, checks for duplicates
3. **Remove User** - FSM state `AdminState.waiting_for_remove_user_id`, deletes from database

## 3. Recent Changes & Implementations

- **ReplyKeyboardMarkup for Main Menu:** Changed from InlineKeyboardMarkup to ReplyKeyboardMarkup with `resize_keyboard=True`. Handlers now listen for `F.text == "📥 Загрузить eSIM"` instead of callback queries.
- **Continuous Upload Loop:** Implemented batch uploading - after successful upload, stays in FSM loop instead of clearing state. Added cancel handler for `/cancel` and "Отмена" text.
- **Provider Auto-Detection:** Added SM-DP+ domain parsing with `PROVIDER_MAPPING` dictionary. Automatically assigns provider if domain matches, skips manual selection.
- **Removed "Invalid QR" Logic:** Deleted the "❌ Невалидный QR" button from issued eSIM keyboard and removed the `mark_invalid` callback handler. The `EsimStatus.INVALID` remains in model for future use.
- **Statistics Update:** Changed from "Выдано/Брак" to "Всего использовано" with breakdown by provider. Removed invalid/defective count.
- **Reset Today's Activity:** Added confirmation dialog mechanism. Reset is implemented by updating `updated_at` to yesterday (using `timedelta(days=1)`) for all eSIMs with `status='issued'` and `updated_at` = today, so they no longer match the `func.date(Esim.updated_at) == today` filter.
- **Document Upload Support:** Extended handler to accept both `F.photo` and `F.document` with image mime_type check.
- **Photo File ID Fallback:** Added try/except around `answer_photo` to catch `TelegramBadRequest` and fall back to text-only message with warning about unavailable photo.
- **Dynamic User Management:** Added `users` table, Admin Panel with user add/remove/list functionality. Authorization now checks both `ALLOWED_USERS` (superadmins) and `users` table (regular users).

## 4. Pending / Future Features (v2)

- **Persistent Image Storage:** Transition from Telegram `file_id` to Google Drive API or local filesystem storage. Current `file_id` approach can fail if Telegram purges old files.
- **Google Sheets Sync:** Sync database records with Google Sheets for external tracking/reporting.
- **Analytics Dashboard:** More detailed statistics, charts, historical data.
- **Notification System:** Alert admins when stock is low.
- **User Roles:** Add roles like "editor", "viewer" with different permissions.
