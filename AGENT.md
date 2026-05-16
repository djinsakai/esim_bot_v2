# Project: Telegram Bot for eSIM Management (eSIM Manager)

## AI Assistant Role
You are a Senior Python Developer. Your task is to write clean, asynchronous, scalable, and secure code for a Telegram bot. Always follow the architectural decisions and rules described in this document. 

## Tech Stack
- **Language:** Python 3.11+
- **Bot Framework:** `aiogram` (version 3.x)
- **Database:** PostgreSQL
- **ORM:** `SQLAlchemy` (v2.0, async via `asyncpg`)
- **Migrations:** `Alembic`
- **QR Scanner:** `WeChatQRCode` (opencv-contrib-python) - CNN-based for robust scanning
- **QR Fallback:** `pyzbar` with image preprocessing pipeline

## Configuration & Authorization (.env)
The bot is private and for internal use only.
1. The `.env` file must contain a list of allowed users: `ALLOWED_USERS=123456789,987654321`.
2. Implement a custom `Filter` or `Middleware` to check `message.from_user.id`.
3. If the user is not in the list, ignore the request or reply: "⛔️ Нет доступа."

## Database Structure (PostgreSQL)
Main table `esims`:
- `id`: Integer, Primary Key
- `lpa_string`: String, Unique (LPA address string, used to prevent duplicates)
- `provider`: String (Operator name: МТС, Билайн, Tele2, etc.)
- `image_file_id`: String (Telegram `file_id`. *Note: we use Telegram servers for MVP file storage*).
- `status`: String, default='available' (Options: `'available'`, `'issued'`, `'invalid'`, `'instant_blocked'`)
- `is_test`: Boolean (False = production, True = test eSIM)
- `supplier`: String, nullable (Supplier tag, e.g., @kardosk or custom)
- `issued_to_user_id`: BigInteger, nullable (Telegram ID of the user who received the eSIM)
- `created_at`: DateTime (Creation date)
- `updated_at`: DateTime (Date of the last status change, automatically updated on issue/return/invalid actions)

**Additional table `users`:**
- `id`: Integer, Primary Key
- `telegram_id`: Integer, Unique (Telegram user ID)
- `username`: String, nullable
- `added_at`: DateTime

## Main Menu (UI)
On `/start`, the bot sends a `ReplyKeyboardMarkup` with persistent keyboard:
1. 📥 **Загрузить eSIM**
2. 🧪 **Загрузить тестовую**
3. 📤 **Получить eSIM**
4. 📊 **Статистика**
5. ⚙️ **Админ-панель** (superadmins only)

Key: Use `is_persistent=True` in ReplyKeyboardMarkup to keep keyboard visible.

## Core Scenarios (Business Logic)

### 1. Upload eSIM
**Goal:** Add a new QR code to the DB, using Telegram servers for image storage.
1. User clicks "📥 **Загрузить eSIM**" or "🧪 **Загрузить тестовую**". 
2. Bot prompts for supplier selection: "Наш" (@kardosk) or "Свой" (custom).
3. Bot downloads the photo to a memory buffer and attempts to parse the QR code.
4. **Robust QR Scanning Pipeline** (in `qr_reader.py`):
   - First: Try WeChatQRCode (CNN-based, handles logos, distortions, inverted colors)
   - Fallback: Try pyzbar with preprocessing (grayscale, invert, CLAHE, threshold)
5. Extract the `lpa_string`. If it already exists in the DB, return error: "❌ Эта eSIM уже есть в базе!".
6. Auto-detect provider from SM-DP+ domain, or ask for manual selection.
7. Save to DB: `image_file_id`, `lpa_string`, `provider`, `status='available'`, `is_test`, `supplier`.
8. Sync to Google Sheets: Status "🟢 Available"

### 2. Get eSIM (Returns & Invalid Handling)
**Goal:** Issue the oldest available eSIM with race condition protection.
1. User clicks "📤 **Получить eSIM**". Bot displays an Inline menu with current stock by provider.
2. After provider selection, fetch the oldest available eSIM: `ORDER BY created_at ASC LIMIT 1 FOR UPDATE`.
3. Change status to `'issued'`, set `issued_to_user_id`, update `updated_at`.
4. Bot sends the user the photo and LPA string with HTML formatting.
5. **Attach an Inline keyboard:**
   - 🔙 **Вернуть в базу**: Returns to available pool (status='available')
   - 🚫 **Не ворк**: Marks as dead, forwards to DEAD_SIM_NOTIFY_CHAT_ID

### 3. Test Section (Slot Input + QA Buttons)
**Goal:** Manage test eSIMs with slot assignment and QA verification.
1. User selects test provider, uploads QR code.
2. Bot sends photo with inline keyboard (Return / Not Working).
3. Bot asks for slot number (e.g., "101").
4. On slot input: Send notification to TEST_SLOT_NOTIFY_CHAT_ID with QA buttons:
   - "✅ Work" (callback: `qa_work:{esim_id}:{slot_number}`)
   - "⛔️ Instant Block" (callback: `qa_instant:{esim_id}:{slot_number}`)
5. QA handlers use `group_router` (no private chat filter) to work in group chats.
6. On "Work": Message updates to compact confirmation.
7. On "Instant Block": Status set to "instant_blocked", Google Sheets updated to "⛔️ Instant".

### 4. Dynamic Statistics
**Goal:** Show current stock and today's activity.
1. User clicks "📊 **Статистика**". Bot calculates metrics from the DB on the fly.
2. "Today's activity" is calculated using the `updated_at` column.
3. Counts BOTH normal and test eSIMs together.
4. Output uses terminal-style format with HTML ParseMode.

### 5. Google Sheets Status Values
| Database Value | Google Sheets Display |
|---------------|----------------------|
| available | 🟢 Available |
| issued | 🔴 Issued |
| invalid | ❌ Invalid |
| instant_blocked | ⛔️ Instant |

### 6. Router Architecture
- `router` - Main handlers with `F.chat.type == "private"` filter
- `group_router` - Handlers for group chat callbacks (no filter)
  - Used for QA buttons in TEST_SLOT_NOTIFY_CHAT_ID
  - Must be included in dp after main router
