import os
import re
import json
from collections import deque
from pathlib import Path


def _int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default

VERSION = "2.5.0"

# Таймзона по умолчанию для организации (можно переопределить переменной окружения)
DEFAULT_TZ_NAME = os.environ.get("ORG_TZ_DEFAULT", "Europe/Moscow")

# Токен бота (задан напрямую в коде)
BOT_TOKEN = "8338879451:AAGTkri6ZXXD88eLAbuOIIqLSVHCoNabVrU"

# Папка для логов и постоянных данных (путь относительно корня проекта)
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
# Подкаталог для лог-файлов
LOGS_DIR = DATA_DIR / "logs"
LOGS_APP_DIR = LOGS_DIR / "app"
LOGS_AUDIT_DIR = LOGS_DIR / "audit"
LOGS_ERROR_DIR = LOGS_DIR / "error"

APP_LOG_RETENTION_DAYS = _int_from_env("APP_LOG_RETENTION_DAYS", 30)
AUDIT_LOG_RETENTION_DAYS = _int_from_env("AUDIT_LOG_RETENTION_DAYS", 30)
ERROR_LOG_MAX_BYTES = _int_from_env("ERROR_LOG_MAX_BYTES", 10 * 1024 * 1024)
ERROR_LOG_BACKUP_COUNT = _int_from_env("ERROR_LOG_BACKUP_COUNT", 10)

# Список администраторов: ID из переменной окружения и логины из data/admins.json
_env_admins = os.environ.get("TELEGRAM_ADMIN_IDS", "")
ADMIN_IDS = {
    int(x)
    for x in re.split(r"[,\s]+", _env_admins.strip())
    if x.strip() and x.lstrip("-").isdigit()
}

# Логины админов (без @) из файла data/admins.json
ADMINS_PATH = DATA_DIR / "admins.json"
try:
    with ADMINS_PATH.open("r", encoding="utf-8") as f:
        ADMIN_USERNAMES = {u.lstrip("@").lower() for u in json.load(f) if isinstance(u, str)}
except FileNotFoundError:
    ADMIN_USERNAMES = {"slonyara"}
    try:
        ADMINS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ADMINS_PATH.open("w", encoding="utf-8") as f:
            json.dump(sorted(ADMIN_USERNAMES), f, ensure_ascii=False, indent=2)
    except Exception:
        pass
else:
    ADMIN_USERNAMES.add("slonyara")

# Логины владельцев (без @) из файла data/owners.json
OWNERS_PATH = DATA_DIR / "owners.json"
try:
    with OWNERS_PATH.open("r", encoding="utf-8") as f:
        OWNER_USERNAMES = {u.lstrip("@").lower() for u in json.load(f) if isinstance(u, str)}
except FileNotFoundError:
    OWNER_USERNAMES = {"panykovc", "slonyara"}
    try:
        OWNERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OWNERS_PATH.open("w", encoding="utf-8") as f:
            json.dump(sorted(OWNER_USERNAMES), f, ensure_ascii=False, indent=2)
    except Exception:
        pass
else:
    OWNER_USERNAMES.add("slonyara")

# Настройки для каждого чата
CFG_PATH = DATA_DIR / "config.json"
# Постоянные напоминания
# Храним в SQLite, для миграции читаем старый JSON
JOBS_DB_PATH = DATA_DIR / "reminders.db"
LEGACY_JOBS_PATH = DATA_DIR / "reminders.json"
# Список зарегистрированных чатов
TARGETS_PATH = DATA_DIR / "chats.json"
# Параметры старого API (для обратной совместимости окружения)
_LEGACY_MAX_BYTES = _int_from_env("BOT_LOG_MAX_BYTES", ERROR_LOG_MAX_BYTES)
_LEGACY_BACKUP = _int_from_env("BOT_LOG_BACKUP_COUNT", ERROR_LOG_BACKUP_COUNT)
_LEGACY_RETENTION = _int_from_env("BOT_LOG_RETENTION_DAYS", APP_LOG_RETENTION_DAYS)

if "ERROR_LOG_MAX_BYTES" not in os.environ:
    ERROR_LOG_MAX_BYTES = _LEGACY_MAX_BYTES
if "ERROR_LOG_BACKUP_COUNT" not in os.environ:
    ERROR_LOG_BACKUP_COUNT = _LEGACY_BACKUP
if "APP_LOG_RETENTION_DAYS" not in os.environ and "BOT_LOG_RETENTION_DAYS" in os.environ:
    APP_LOG_RETENTION_DAYS = _LEGACY_RETENTION
if "AUDIT_LOG_RETENTION_DAYS" not in os.environ and "BOT_LOG_RETENTION_DAYS" in os.environ:
    AUDIT_LOG_RETENTION_DAYS = _LEGACY_RETENTION

# Окно дедупликации (30 записей ≈ 30 секунд)
recent_signatures = deque(maxlen=30)

# Шаблон текста напоминания (номер заявки добавляется с пробелом при наличии)
REMINDER_TEMPLATE = "{date} {type} {time} {room}{ticket}"

# Регэксп для строки встречи: день, месяц, тип, время, аудитория и опциональный номер
MEETING_REGEX = re.compile(
    r"^\s*(\d{1,2})[.\-/](\d{1,2})\s+(\S+)\s+(\d{1,2}[:.]\d{2})\s+(\S+)(?:\s+(.+?))?\s*$",
    re.IGNORECASE,
)

CB_MENU = "menu"
CB_SETTINGS = "settings"
CB_ACTIVE = "active"
CB_ACTIVE_PAGE = "active_page"   # active_page:<номер>
CB_MY = "my"
CB_MY_PAGE = "my_page"
CB_CREATE = "create"
CB_HELP = "help"

CB_SET_TZ = "set_tz"
CB_SET_TZ_LOCAL = "set_tz_local"
CB_SET_TZ_MOSCOW = "set_tz_eu_msk"
CB_SET_TZ_CHICAGO = "set_tz_us_chi"
CB_SET_TZ_ENTER = "set_tz_enter"

CB_SET_OFFSET = "set_offset"
CB_OFF_DEC = "off_dec"
CB_OFF_INC = "off_inc"
CB_OFF_PRESET_10 = "off_p10"
CB_OFF_PRESET_15 = "off_p15"
CB_OFF_PRESET_20 = "off_p20"
CB_OFF_PRESET_30 = "off_p30"

CB_CHATS = "chats"            # список зарегистрированных чатов
CB_CHAT_DEL = "chat_del"      # chat_del:<chat_id>:<topic_id>
CB_ARCHIVE = "archive"
CB_ARCHIVE_PAGE = "archive_page"
CB_ARCHIVE_CLEAR = "archive_clear"
CB_ARCHIVE_CLEAR_CONFIRM = "archive_clear_yes"

CB_CANCEL = "cancel"  # cancel:<id_задачи>
CB_SHIFT = "shift"    # shift:<id_задачи>:минуты
CB_SENDNOW = "send"   # send:<id_задачи> — отправить немедленно
CB_ACTIONS = "act"     # act:<id_задачи> — меню действий
CB_DISABLED = "noop"   # префикс замороженных кнопок: noop:<random>
CB_NOOP = CB_DISABLED  # обратная совместимость с новым кодом

# Выбор чата при создании напоминания из лички
CB_PICK_CHAT = "pick_chat"  # pick_chat:<chat_id>:<topic_id>:<token>

# Управление администраторами
CB_ADMINS = "admins"           # просмотр списка
CB_ADMIN_ADD = "adm_add"       # запрос логина
CB_ADMIN_DEL = "adm_del"       # adm_del:<username>

# Повторяемость
CB_RRULE = "rrule"         # rrule:id_задачи:<once|daily|weekly>
RR_ONCE = "once"
RR_DAILY = "daily"
RR_WEEKLY = "weekly"

# Флаги ожидания ввода
AWAIT_TZ = "await_tz"
AWAIT_ADMIN = "await_admin"  # ждём @username для добавления в админы

# Окно догонки
CATCHUP_WINDOW_SECONDS = 5 * 60  # 5 минут

# Пагинация
PAGE_SIZE = 10
