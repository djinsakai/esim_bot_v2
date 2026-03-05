# eSIM Manager Bot

Telegram bot for managing eSIM inventory with QR code scanning, user management, and statistics.

## Features

- 📥 **Upload eSIM** - Scan QR codes from photos or documents
- 🔄 **Auto-detect provider** - Automatically identifies operator from SM-DP+ domain
- 📤 **Get eSIM** - Issue available eSIMs to users
- 📊 **Statistics** - Track inventory and daily activity
- ⚙️ **Admin Panel** - Manage user access (superadmins only)
- 🔒 **Authorization** - Two-level access (superadmins + database users)

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
```

## Bot Commands

- `/start` - Show main menu
- `/cancel` - Cancel current action

### Main Menu (Reply Keyboard)
- 📥 Загрузить eSIM - Upload new QR code
- 📤 Получить eSIM - Get available eSIM
- 📊 Статистика - View statistics
- ⚙️ Админ-панель - Admin panel (superadmins only)

## Project Structure

```
├── app/
│   ├── bot/
│   │   ├── handlers.py    # All bot handlers
│   │   ├── filters.py     # Authorization filter
│   │   └── middleware.py  # Auth middleware
│   ├── db/
│   │   ├── models.py      # SQLAlchemy models
│   │   └── database.py    # DB connection
│   ├── utils/
│   │   ├── config.py      # Configuration loader
│   │   └── qr_reader.py   # QR code recognition
│   └── main.py            # Bot entry point
├── alembic/               # Database migrations
├── MEMORY.md              # Developer documentation
└── requirements.txt        # Python dependencies
```

## Tech Stack

- **aiogram 3** - Telegram bot framework
- **SQLAlchemy 2.0** - ORM with async support
- **asyncpg** - Async PostgreSQL driver
- **OpenCV + pyzbar** - QR code recognition
- **Alembic** - Database migrations

## License

MIT
