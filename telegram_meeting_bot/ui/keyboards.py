from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from ..core.constants import (
    CB_ACTIONS,
    CB_ACTIVE,
    CB_ACTIVE_PAGE,
    CB_ADMIN_ADD,
    CB_ADMIN_DEL,
    CB_ADMINS,
    CB_ARCHIVE,
    CB_ARCHIVE_CLEAR,
    CB_ARCHIVE_CLEAR_CONFIRM,
    CB_ARCHIVE_PAGE,
    CB_CANCEL,
    CB_CHAT_DEL,
    CB_CHATS,
    CB_CREATE,
    CB_HELP,
    CB_MENU,
    CB_MY,
    CB_MY_PAGE,
    CB_OFF_DEC,
    CB_OFF_INC,
    CB_OFF_PRESET_10,
    CB_OFF_PRESET_15,
    CB_OFF_PRESET_20,
    CB_OFF_PRESET_30,
    CB_PICK_CHAT,
    CB_RRULE,
    CB_SENDNOW,
    CB_SET_OFFSET,
    CB_SET_TZ,
    CB_SET_TZ_CHICAGO,
    CB_SET_TZ_ENTER,
    CB_SET_TZ_LOCAL,
    CB_SET_TZ_MOSCOW,
    CB_SETTINGS,
    CB_SHIFT,
    RR_DAILY,
    RR_ONCE,
    RR_WEEKLY,
)


def main_menu_kb(
    is_admin: bool = False,
    *,
    allow_settings: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🆕 Создать встречу", callback_data=CB_CREATE)],
        [InlineKeyboardButton(text="📂 Мои встречи", callback_data=CB_MY)],
    ]
    if is_admin:
        rows[-1].append(InlineKeyboardButton(text="📝 Активные", callback_data=CB_ACTIVE))
        rows.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data=CB_SETTINGS)])
    elif allow_settings:
        rows.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data=CB_SETTINGS)])
    rows.append([InlineKeyboardButton(text="❓ Справка", callback_data=CB_HELP)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reply_menu_kb(
    is_admin: bool = False,
    *,
    allow_settings: bool = False,
) -> ReplyKeyboardMarkup:
    """Отдельная клавиатура под строкой ввода с ключевыми действиями."""

    rows: list[list[KeyboardButton]] = [
        [
            KeyboardButton(text="➕ Создать встречу"),
            KeyboardButton(text="📂 Мои встречи"),
        ]
    ]
    if is_admin:
        rows.append(
            [
                KeyboardButton(text="📝 Активные"),
                KeyboardButton(text="⚙️ Настройки"),
            ]
        )
    elif allow_settings:
        rows.append([KeyboardButton(text="⚙️ Настройки")])
    rows.append([KeyboardButton(text="❓ Справка")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выберите действие…",
    )


def settings_menu_kb(is_owner: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🕒 Таймзона", callback_data=CB_SET_TZ)],
        [InlineKeyboardButton(text="⏳ Оффсет (мин)", callback_data=CB_SET_OFFSET)],
        [InlineKeyboardButton(text="📋 Чаты", callback_data=CB_CHATS)],
        [InlineKeyboardButton(text="📦 Архив", callback_data=CB_ARCHIVE)],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton(text="👥 Админы", callback_data=CB_ADMINS)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tz_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Локальная ОС", callback_data=CB_SET_TZ_LOCAL)],
            [InlineKeyboardButton(text="Europe/Moscow", callback_data=CB_SET_TZ_MOSCOW)],
            [InlineKeyboardButton(text="America/Chicago", callback_data=CB_SET_TZ_CHICAGO)],
            [InlineKeyboardButton(text="Ввести вручную", callback_data=CB_SET_TZ_ENTER)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_SETTINGS)],
        ]
    )


def offset_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="−5", callback_data=CB_OFF_DEC),
                InlineKeyboardButton(text="+5", callback_data=CB_OFF_INC),
            ],
            [
                InlineKeyboardButton(text="10", callback_data=CB_OFF_PRESET_10),
                InlineKeyboardButton(text="15", callback_data=CB_OFF_PRESET_15),
                InlineKeyboardButton(text="20", callback_data=CB_OFF_PRESET_20),
                InlineKeyboardButton(text="30", callback_data=CB_OFF_PRESET_30),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_SETTINGS)],
        ]
    )


def chats_menu_kb(known_chats: list | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if known_chats:
        for chat in known_chats:
            chat_id = chat.get("chat_id")
            topic_id = chat.get("topic_id") or 0
            title = chat.get("title") or str(chat_id)
            rows.append(
                [
                    InlineKeyboardButton(text=title, callback_data=CB_CHATS),
                    InlineKeyboardButton(
                        text="❌",
                        callback_data=f"{CB_CHAT_DEL}:{chat_id}:{topic_id}",
                    ),
                ]
            )
    else:
        rows.append([InlineKeyboardButton(text="(пусто)", callback_data=CB_CHATS)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_SETTINGS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def job_kb(job_id: str, rrule: str = RR_ONCE) -> InlineKeyboardMarkup:
    label = {
        RR_ONCE: "🔁 Разово",
        RR_DAILY: "🔁 Ежедневно",
        RR_WEEKLY: "🔁 Еженедельно",
    }.get(rrule, "🔁 Разово")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"{CB_CANCEL}:{job_id}")],
            [
                InlineKeyboardButton(text="➕ +5 мин", callback_data=f"{CB_SHIFT}:{job_id}:5"),
                InlineKeyboardButton(text="➕ +10 мин", callback_data=f"{CB_SHIFT}:{job_id}:10"),
            ],
            [InlineKeyboardButton(text=label, callback_data=f"{CB_RRULE}:{job_id}:{rrule}")],
        ]
    )


def choose_chat_kb(chats: list, token: str, *, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for chat in chats:
        chat_id = chat.get("chat_id")
        topic_id = chat.get("topic_id") or 0
        title = chat.get("title") or str(chat_id)
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"{CB_PICK_CHAT}:{chat_id}:{topic_id}:{token}",
                )
            ]
        )
    if is_admin:
        rows.append([InlineKeyboardButton(text="📝 Активные", callback_data=CB_ACTIVE)])
    rows.append([InlineKeyboardButton(text="❓ Справка", callback_data=CB_HELP)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def active_kb(
    chunk: list,
    page: int,
    pages_total: int,
    uid: int,
    is_admin: bool = False,
    *,
    page_prefix: str = CB_ACTIVE_PAGE,
    view: str = "all",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for job in chunk:
        job_id = job.get("job_id")
        if not job_id:
            continue
        if is_admin or job.get("author_id") == uid:
            label = job.get("text", "")
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"⚙️ {label}", callback_data=f"{CB_ACTIONS}:{job_id}:{view}"
                    )
                ]
            )
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{page_prefix}:{page-1}"))
    if page < pages_total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{page_prefix}:{page+1}"))
    if nav:
        rows.append(nav)
    else:
        rows.append([InlineKeyboardButton(text="⟲ Обновить", callback_data=f"{page_prefix}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def archive_kb(
    page: int,
    pages_total: int,
    *,
    has_entries: bool,
    can_clear: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_ARCHIVE_PAGE}:{page-1}"))
    if page < pages_total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_ARCHIVE_PAGE}:{page+1}"))
    if nav:
        rows.append(nav)
    else:
        rows.append([InlineKeyboardButton(text="⟲ Обновить", callback_data=f"{CB_ARCHIVE_PAGE}:{page}")])
    if can_clear and has_entries:
        rows.append([InlineKeyboardButton(text="🧹 Очистить", callback_data=CB_ARCHIVE_CLEAR)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_SETTINGS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def archive_clear_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, очистить", callback_data=CB_ARCHIVE_CLEAR_CONFIRM)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_ARCHIVE)],
        ]
    )


def actions_kb(
    job_id: str,
    is_admin: bool = False,
    *,
    return_to: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📤 Отправить сейчас", callback_data=f"{CB_SENDNOW}:{job_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"{CB_CANCEL}:{job_id}")],
    ]
    if is_admin:
        rows.append(
            [
                InlineKeyboardButton(text="➕ +5", callback_data=f"{CB_SHIFT}:{job_id}:5"),
                InlineKeyboardButton(text="➕ +10", callback_data=f"{CB_SHIFT}:{job_id}:10"),
            ]
        )
    suffix = f":{return_to}" if return_to else ""
    rows.append(
        [InlineKeyboardButton(text="↩️ Назад", callback_data=f"{CB_ACTIONS}:{job_id}:close{suffix}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admins_menu_kb(admins: set[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for name in sorted(admins):
        rows.append(
            [InlineKeyboardButton(text=f"❌ @{name}", callback_data=f"{CB_ADMIN_DEL}:{name}")]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data=CB_ADMIN_ADD)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_SETTINGS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def panel_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    return main_menu_kb(is_admin)
