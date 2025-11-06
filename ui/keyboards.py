from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from ..core.constants import (
    CB_ACTIVE,
    CB_ACTIVE_PAGE,
    CB_CANCEL,
    CB_HELP,
    CB_MENU,
    CB_OFF_DEC,
    CB_OFF_INC,
    CB_OFF_PRESET_10,
    CB_OFF_PRESET_15,
    CB_OFF_PRESET_20,
    CB_OFF_PRESET_30,
    CB_RRULE,
    CB_SET_OFFSET,
    CB_SET_TZ,
    CB_SET_TZ_CHICAGO,
    CB_SET_TZ_ENTER,
    CB_SET_TZ_LOCAL,
    CB_SET_TZ_MOSCOW,
    CB_SETTINGS,
    CB_SHIFT,
    CB_PICK_CHAT,
    CB_CHATS,
    CB_CHAT_DEL,
    CB_ADMINS,
    CB_ADMIN_ADD,
    CB_ADMIN_DEL,
    CB_SENDNOW,
    CB_ACTIONS,
    RR_ONCE,
    RR_DAILY,
    RR_WEEKLY,
)


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("📝 Активные", callback_data=CB_ACTIVE)]]
    if is_admin:
        rows.append([InlineKeyboardButton("⚙️ Настройки", callback_data=CB_SETTINGS)])
    rows.append([InlineKeyboardButton("❓ Справка", callback_data=CB_HELP)])
    return InlineKeyboardMarkup(rows)


def settings_menu_kb(is_owner: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🕒 Таймзона", callback_data=CB_SET_TZ)],
        [InlineKeyboardButton("⏳ Оффсет (мин)", callback_data=CB_SET_OFFSET)],
        [InlineKeyboardButton("📋 Чаты", callback_data=CB_CHATS)],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton("👥 Админы", callback_data=CB_ADMINS)])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(rows)


def tz_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Локальная ОС", callback_data=CB_SET_TZ_LOCAL)],
        [InlineKeyboardButton("Europe/Moscow", callback_data=CB_SET_TZ_MOSCOW)],
        [InlineKeyboardButton("America/Chicago", callback_data=CB_SET_TZ_CHICAGO)],
        [InlineKeyboardButton("Ввести вручную", callback_data=CB_SET_TZ_ENTER)],
        [InlineKeyboardButton("⬅️ Назад", callback_data=CB_SETTINGS)],
    ])


def offset_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("−5", callback_data=CB_OFF_DEC),
         InlineKeyboardButton("+5", callback_data=CB_OFF_INC)],
        [InlineKeyboardButton("10", callback_data=CB_OFF_PRESET_10),
         InlineKeyboardButton("15", callback_data=CB_OFF_PRESET_15),
         InlineKeyboardButton("20", callback_data=CB_OFF_PRESET_20),
         InlineKeyboardButton("30", callback_data=CB_OFF_PRESET_30)],
        [InlineKeyboardButton("⬅️ Назад", callback_data=CB_SETTINGS)],
    ])


def chats_menu_kb(known_chats: list | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if known_chats:
        for c in known_chats:
            cid = c.get("chat_id")
            topic = c.get("topic_id")
            title = c.get("title") or str(cid)
            rows.append([
                InlineKeyboardButton(title, callback_data=CB_CHATS),
                InlineKeyboardButton("❌", callback_data=f"{CB_CHAT_DEL}:{cid}:{topic or 0}")
            ])
    else:
        rows.append([InlineKeyboardButton("(пусто)", callback_data=CB_CHATS)])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=CB_SETTINGS)])
    return InlineKeyboardMarkup(rows)


def job_kb(job_id: str, rrule: str = RR_ONCE) -> InlineKeyboardMarkup:
    rr_label = {"once": "🔁 Разово", "daily": "🔁 Ежедневно", "weekly": "🔁 Еженедельно"}.get(rrule, "🔁 Разово")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отменить", callback_data=f"{CB_CANCEL}:{job_id}")],
        [InlineKeyboardButton("➕ +5 мин", callback_data=f"{CB_SHIFT}:{job_id}:5"),
         InlineKeyboardButton("➕ +10 мин", callback_data=f"{CB_SHIFT}:{job_id}:10")],
        [InlineKeyboardButton(rr_label, callback_data=f"{CB_RRULE}:{job_id}:{rrule}")]
    ])


def choose_chat_kb(chats: list, token: str) -> InlineKeyboardMarkup:
    rows = []
    for c in chats:
        cid = c.get("chat_id")
        topic = c.get("topic_id")
        title = c.get("title") or str(cid)
        rows.append([
            InlineKeyboardButton(title, callback_data=f"{CB_PICK_CHAT}:{cid}:{topic or 0}:{token}")
        ])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(rows)


def active_kb(
    chunk: list,
    page: int,
    pages_total: int,
    uid: int,
    is_admin: bool = False,
) -> InlineKeyboardMarkup:
    rows = []
    for j in chunk:
        jid = j.get("job_id")
        if is_admin or j.get("author_id") == uid:
            label = j.get("text", "")
            rows.append([
                InlineKeyboardButton(
                    f"⚙️ {label}", callback_data=f"{CB_ACTIONS}:{jid}"
                )
            ])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"{CB_ACTIVE_PAGE}:{page-1}"))
    if page < pages_total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"{CB_ACTIVE_PAGE}:{page+1}"))
    if nav:
        rows.append(nav)
    else:
        rows.append([InlineKeyboardButton("⟲ Обновить", callback_data=f"{CB_ACTIVE_PAGE}:{page}")])
    return InlineKeyboardMarkup(rows)


def actions_kb(jid: str, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📤 Отправить сейчас", callback_data=f"{CB_SENDNOW}:{jid}")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"{CB_CANCEL}:{jid}")],
    ]
    if is_admin:
        rows.append([
            InlineKeyboardButton("➕ +5", callback_data=f"{CB_SHIFT}:{jid}:5"),
            InlineKeyboardButton("➕ +10", callback_data=f"{CB_SHIFT}:{jid}:10"),
        ])
    rows.append([
        InlineKeyboardButton("↩️ Назад", callback_data=f"{CB_ACTIONS}:{jid}:close")
    ])
    return InlineKeyboardMarkup(rows)


def panel_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    return main_menu_kb(is_admin)


def admins_menu_kb(admins: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for name in sorted(admins):
        rows.append([
            InlineKeyboardButton(f"❌ @{name}", callback_data=f"{CB_ADMIN_DEL}:{name}")
        ])
    rows.append([InlineKeyboardButton("➕ Добавить", callback_data=CB_ADMIN_ADD)])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=CB_SETTINGS)])
    return InlineKeyboardMarkup(rows)
