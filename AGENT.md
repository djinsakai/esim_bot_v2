# Project: Telegram Bot for eSIM Management (eSIM Manager)

## AI Assistant Role
You are a Senior Python Developer. Your task is to write clean, asynchronous, scalable, and secure code for a Telegram bot. Always follow the architectural decisions and rules described in this document. 

## Tech Stack
- **Language:** Python 3.11+
- **Bot Framework:** `aiogram` (version 3.x)
- **Database:** PostgreSQL
- **ORM:** `SQLAlchemy` (v2.0, async via `asyncpg`)
- **Migrations:** `Alembic`
- **Image & QR Processing:** `OpenCV` (`cv2`), `pyzbar` or `qreader` (MUST support processing of inverted colors).

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
- `status`: String, default='available' (Options: `'available'`, `'issued'`, `'invalid'`)
- `issued_to_user_id`: BigInteger, nullable (Telegram ID of the user who received/invalidated the eSIM)
- `created_at`: DateTime (Creation date)
- `updated_at`: DateTime (Date of the last status change, automatically updated on issue/return/invalid actions)

## Main Menu (UI)
On `/start`, the bot sends a `ReplyKeyboardMarkup` with 3 main buttons:
1. 📥 **Загрузить eSIM**
2. 📤 **Получить eSIM**
3. 📊 **Статистика**

## Core Scenarios (Business Logic)

### 1. Upload eSIM
**Goal:** Add a new QR code to the DB, using Telegram servers for image storage.
1. User clicks "📥 **Загрузить eSIM**". Bot asks for a photo/screenshot of the QR code.
2. Bot downloads the photo to a memory buffer and attempts to parse the QR code. It must try standard reading first; if it fails, it MUST apply color inversion (`cv2.bitwise_not()`) and try again.
3. Extract the `lpa_string`. If it already exists in the DB, return error: "❌ Эта eSIM уже есть в базе!".
4. Bot prompts the user to select an operator via Inline buttons (МТС, Билайн, Мегафон, Tele2, etc.).
5. Save to DB: `image_file_id` (from the Message object), `lpa_string`, `provider`, `status='available'`. Send a success message.

### 2. Get eSIM (Returns & Invalid Handling)
**Goal:** Issue the oldest available eSIM with race condition protection.
1. User clicks "📤 **Получить eSIM**". Bot displays an Inline menu with current stock. Example: *МТС (5 шт.), Билайн (2 шт.)*. Buttons for providers with 0 stock should be hidden or disabled.
2. After provider selection, fetch the oldest available eSIM: `ORDER BY created_at ASC LIMIT 1 FOR UPDATE`.
3. Change status to `'issued'`, set `issued_to_user_id`, update `updated_at` to current timestamp.
4. Bot sends the user the photo (using `image_file_id`) and the monospaced text `{lpa_string}`.
5. **Attach an Inline keyboard beneath the issued eSIM:**
   - 🔙 **Вернуть в базу**: Changes status to `'available'`, clears `issued_to_user_id`, updates `updated_at`. (Action restricted ONLY to the user who took it).
   - ❌ **Невалидный QR**: Changes status to `'invalid'`, updates `updated_at`. (Action restricted ONLY to the user who took it).

### 3. Dynamic Statistics
**Goal:** Show current stock and today's activity.
1. User clicks "📊 **Статистика**". Bot calculates metrics from the DB on the fly.
2. "Today's activity" is calculated using the `updated_at` column (where date matches `CURRENT_DATE`).
3. Bot replies with the following exact template:
   ```text
   📊 Наличие в базе:
   🟢 Всего доступно: 45 шт.
   - МТС: 20
   - Билайн: 15
   - Tele2: 10
   
   📈 Активность за СЕГОДНЯ:
   ✅ Выдано: 12 шт.
   ❌ Отправлено в брак: 2 шт.
