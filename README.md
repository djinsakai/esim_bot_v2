# eSIM Manager Bot

Telegram bot for managing eSIM inventory with QR code scanning, supplier tracking, user management, and CRM integration.

## Features

- 📥 **Upload eSIM** - Scan QR codes with supplier selection (our team or custom)
- 🔄 **Auto-detect provider** - Automatically identifies operator from SM-DP+ domain
- 📦 **Supplier Tracking** - Track supplier for each eSIM (@kardosk or custom)
- 📤 **Get eSIM** - Issue available eSIMs to users with inline keyboard
- 📊 **Statistics** - Track inventory and daily activity
- 🧪 **Test Section** - Separate test eSIM flow with slot number input
- 🚫 **Dead SIM Reporting** - Report broken eSIMs directly to admin chat
- ⚙️ **Admin Panel** - Manage user access (superadmins only)
- 🔒 **Authorization** - Two-level access (superadmins + database users)
- 📑 **Google Sheets Sync** - Real-time CRM dashboard

## Requirements

- Python 3.10+
- PostgreSQL 14+
- Telegram Bot Token

## Installation

1. **Clone and install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Configure environment:**
```bash
cp .env.example .env
# Edit .env with your settings
```

3. **Set up database:**
```sql
CREATE DATABASE esim;
```

4. **Run migrations:**
```bash
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

5. **Start the bot:**
```bash
python -m app.main
```

## Configuration (.env)

```env
# Telegram Bot
BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=123456789,987654321  # Superadmin IDs (comma-separated)

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=esim
DB_USER=postgres
DB_PASSWORD=your_password
# Or use full URL:
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Google Sheets (optional - for CRM sync)
GOOGLE_SHEET_ID=your_spreadsheet_id
GOOGLE_CREDENTIALS_PATH=credentials.json

# Supplier Settings
OUR_SUPPLIER_NAME=@kardosk  # Default supplier for "our team" uploads

# Notification Chats (group chat IDs, MUST start with -100)
# IMPORTANT: Get the correct group chat ID by forwarding a message from your group to the bot.
# Group IDs start with -100 (e.g., -1001234567890)
TEST_SLOT_NOTIFY_CHAT_ID=-100XXXXXXXXX  # Notifies on successful test slot assignment
DEAD_SIM_NOTIFY_CHAT_ID=-100XXXXXXXXX   # Notifies on dead/broken eSIM reports
```

## Bot Commands

- `/start` - Show main menu (works in private chats only)
- `/cancel` - Cancel current action

### Main Menu (Reply Keyboard)
- 📥 Загрузить eSIM - Upload new QR code (select supplier first)
- 🧪 Загрузить тестовую - Upload test eSIM
- 📤 Получить eSIM - Get available eSIM
- 📊 Статистика - View statistics
- ⚙️ Админ-панель - Admin panel (superadmins only)

### eSIM Issue Flow
All issued eSIMs include inline keyboard:
- 🔙 Вернуть в базу - Return eSIM to available pool
- 🚫 Не ворк - Report as dead/broken (forwards to admin chat)

### Test Section Flow
1. User selects test eSIM provider
2. Bot sends eSIM photo with keyboard
3. Bot asks: "Введите номер слота"
4. User enters slot number (e.g., "101")
5. Bot notifies TEST_SLOT_NOTIFY_CHAT_ID with formatted message

## Project Structure

```
├── app/
│   ├── bot/
│   │   ├── handlers.py    # All bot handlers (FSM, callbacks, messages)
│   │   ├── filters.py     # Authorization filter
│   │   └── middleware.py  # Auth middleware
│   ├── db/
│   │   ├── models.py      # SQLAlchemy models (Esim, User)
│   │   └── database.py    # DB connection (asyncpg)
│   ├── utils/
│   │   ├── config.py      # Configuration loader (.env)
│   │   └── qr_reader.py   # QR code recognition (OpenCV + pyzbar)
│   ├── services/
│   │   └── google_sheets.py  # Google Sheets CRM sync
│   └── main.py            # Bot entry point
├── alembic/               # Database migrations
├── MEMORY.md              # Developer documentation & changelog
└── requirements.txt       # Python dependencies
```

## Google Sheets CRM Format

Columns (8 total):
1. ID
2. Дата загрузки
3. Оператор
4. LPA строка
5. Поставщик
6. Статус
7. Кому выдана
8. Дата выдачи

## Tech Stack

- **aiogram 3** - Telegram bot framework
- **SQLAlchemy 2.0** - ORM with async support
- **asyncpg** - Async PostgreSQL driver
- **OpenCV + pyzbar** - QR code recognition (supports inverted colors)
- **Alembic** - Database migrations
- **gspread** - Google Sheets API

## License

MIT
