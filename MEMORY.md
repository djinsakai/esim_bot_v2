# Project Memory & Changelog

## 1. Architecture Overview

This is a Telegram bot for eSIM management built with:
- **Framework:** aiogram 3.x (async Python)
- **Database:** PostgreSQL with SQLAlchemy 2.0 (async via asyncpg)
- **Image Processing:** OpenCV (cv2) + pyzbar for QR code recognition
- **Migrations:** Alembic
- **Google Sheets:** gspread for CRM sync

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
    is_test: bool                       # False = production, True = test eSIM
    supplier: str | None                # Supplier tag (e.g., @kardosk or custom)
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

### Test eSIM System
The bot supports separate test eSIMs that are segregated from production stock but counted together in statistics.

**Upload Flow:**
- Main menu has "🧪 Загрузить тестовую" button
- When clicked, FSM state data is set with `is_test=True`
- Same auto-detection and QR parsing logic applies
- After successful save, broadcasts notification to all authorized users:
  ```
  ⚠️ <b>Внимание!</b> Загружена новая тестовая eSIM (Оператор: {provider}). Пожалуйста, проверьте её в меню получения.
  ```
- FSM loop continues with `is_test` flag preserved

**Issue Flow:**
- "📤 Получить eSIM" shows normal stock by provider
- If test eSIMs exist, shows additional button "🧪 Тестовые eSIM (N шт.)"
- Clicking test button shows provider selection for test eSIMs only
- Test eSIM issuance includes "(ТЕСТОВАЯ)" in the message caption
- Uses same `SELECT ... FOR UPDATE` lock mechanism

**Statistics:**
- Counts BOTH normal and test eSIMs together (no filtering by `is_test`)
- Total Available = normal + test
- Today's Activity = normal + test

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
- **Test eSIM System:** Added ability to upload and issue separate "test" eSIMs.
  - New `is_test` boolean column in `esims` table (default False)
  - Main menu updated: "📥 Загрузить eSIM" | "🧪 Загрузить тестовую"
  - Upload flow uses FSM state data to track `is_test` flag
  - Broadcast notification sent to all users when test eSIM is uploaded
  - Issue menu separates normal and test stock with "🧪 Тестовые eSIM" button
  - Test eSIMs display "(ТЕСТОВАЯ)" in caption
  - Statistics counts both normal and test eSIMs together
- **Statistics Date Formatting:** Changed "СЕГОДНЯ" to actual date in DD.MM.YY format (e.g., "07.03.26").
- **Statistics UI Redesign:** Redesigned statistics output to terminal-style with pseudo-graphics:
  - Header: `<b>📊 Сводка eSIM</b>`
  - Separator: `━━━━━━━━━━━━━━━━━━`
  - Available section with tree branches (├ for non-last, └ for last)
  - Issued section with date in format `📈 Выдано (DD.MM.YY): <b>{count}</b>`
  - Uses `ParseMode.HTML` for bold text
- **FSM is_test Flag Bug Fix:** Explicitly set `is_test=False` in normal "📥 Загрузить eSIM" handler to prevent flag leakage from previous FSM sessions.
- **Duplicate Check Fix:** Added duplicate check to `process_provider` handler (manual provider selection). Both auto-detect and manual paths now check for duplicates before insert.
- **Google Sheets CRM Sync:** Implemented real-time synchronization with Google Sheets as live CRM dashboard.
  - Sheet columns: ID | Дата загрузки | Оператор | LPA строка | Ссылка на QR-код | Статус | Кому выдана | Дата выдачи
  - `append_new_esim()` called on upload (async via `asyncio.create_task`)
  - `update_esim_status()` called on issue/return (async via `asyncio.create_task`)
  - Issue updates: Status → "🔴 Выдана", User → "@username (id)", Date → "DD.MM.YYYY HH:MM"
  - Return updates: Status → "🟢 Доступна", User → "", Date → ""
  - All Google API calls are fire-and-forget; failures are logged but don't interrupt Telegram flow
  - Config: `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS_PATH` in .env
- **User List Real Usernames:** Admin panel "Список пользователей" now fetches real usernames via `bot.get_chat(telegram_id)`, limited to last 20 users to avoid rate limits.
- **Supplier Selection (Upload):** When uploading eSIMs, user selects between "Наш" (our team) or "Свой поставщик" (custom).
  - Our team uses `OUR_SUPPLIER_NAME` from config (default: @kardosk)
  - Custom allows user to input any supplier tag
  - New FSM states: `selecting_supplier`, `waiting_for_supplier_name`
- **Supplier Column:** Added `supplier` column to `esims` table via Alembic migration.
- **Google Sheets 8-Column Format:** Updated sheet columns: ID | Дата загрузки | Оператор | LPA строка | Поставщик | Статус | Кому выдана | Дата выдачи
- **Test Section Slot Input:** Test eSIMs require slot number input before confirmation.
  - User receives eSIM photo with inline keyboard (Return / Not Working)
  - Bot asks for slot number (e.g., 101)
  - On success: notifies TEST_SLOT_NOTIFY_CHAT_ID with formatted message
  - On "Не ворк": forwards to DEAD_SIM_NOTIFY_CHAT_ID with full eSIM data
- **Inline "Не ворк" Button:** All issued eSIMs now have dual inline keyboard buttons:
  - "🔙 Вернуть в базу" - returns eSIM to available pool
  - "🚫 Не ворк" - marks as dead and forwards to admin chat
- **Dead eSIM Notifications:** When user clicks "Не ворк", bot:
  - Updates status in DB and Google Sheets to "❌ Не работает"
  - Sends photo + caption to DEAD_SIM_NOTIFY_CHAT_ID
  - Edits original message to show "(Отмечена как нерабочая)"
- **Chat Type Filter (TEMPORARILY DISABLED):** Originally added to restrict bot to private chats only.
  - `router.message.filter(F.chat.type == "private")`
  - `router.callback_query.filter(F.chat.type == "private")`
  - **BUG DISCOVERED:** This filter was SILENTLY DROPPING all CallbackQuery responses from inline buttons!
  - The filter prevented callback handlers from ever receiving the callback, causing buttons to appear to do nothing.
  - **FIX:** Commented out the filter temporarily. Re-enable after debugging complete.
  - Group notifications still work via direct bot.send_message calls
- **Bug Fix - AllowedUserFilter:** Updated to handle both Message and CallbackQuery types.
- **Bug Fix - Database:** Changed to NullPool to avoid connection leaks.
- **Bug Fix - Test Upload:** Fixed is_test flag being lost when state.clear() was called in supplier handlers. Now preserves is_test before clearing state.
- **Global Error Handler:** Added in main.py to catch and print all exceptions.
- **Debug Handler for Group Chat ID:** Added handler to capture correct group chat ID when user forwards a message from the group.
- **Chat Type Filter (TEMPORARILY DISABLED):** Disabled to allow callback queries to work. Re-enable after full testing.

## 4. Configuration (.env variables)

```env
# Telegram Bot
BOT_TOKEN=your_bot_token
ALLOWED_USERS=123456789,987654321  # Superadmin IDs (comma-separated)

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=esim
DB_USER=postgres
DB_PASSWORD=your_password
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Google Sheets (optional)
GOOGLE_SHEET_ID=your_sheet_id
GOOGLE_CREDENTIALS_PATH=credentials.json

# Supplier Settings
OUR_SUPPLIER_NAME=@kardosk

# Notification Chats
# IMPORTANT: Get the correct group chat ID by forwarding a message from your group to the bot.
# Group chat IDs start with -100 (e.g., -1001234567890)
TEST_SLOT_NOTIFY_CHAT_ID=-100XXXXXXXXX  # Group for successful test slot notifications
DEAD_SIM_NOTIFY_CHAT_ID=-100XXXXXXXXX   # Group for dead/broken eSIM notifications
```

## 5. Pending / Future Features (v2)

- **Persistent Image Storage:** Transition from Telegram `file_id` to Google Drive API or local filesystem storage. Current `file_id` approach can fail if Telegram purges old files.
- **Analytics Dashboard:** More detailed statistics, charts, historical data.
- **Notification System:** Alert admins when stock is low.
- **User Roles:** Add roles like "editor", "viewer" with different permissions.
