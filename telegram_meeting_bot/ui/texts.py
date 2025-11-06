from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Dict, Iterable

import pytz

from ..core.constants import RR_DAILY, RR_ONCE, RR_WEEKLY, VERSION
from ..core.storage import (
    get_jobs_store,
    get_known_chats,
    get_offset_for_chat,
    resolve_tz_for_chat,
)


def escape_md(text: str) -> str:
    """Экранировать спецсимволы Markdown в динамике."""

    if not text:
        return ""
    replacements = (
        ("\\", "\\\\"),
        ("_", "\\_"),
        ("*", "\\*"),
        ("[", "\\["),
        ("]", "\\]"),
        ("(", "\\("),
        (")", "\\)"),
        ("`", "\\`"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def menu_text_for(chat_id: int) -> str:
    tz = resolve_tz_for_chat(chat_id)
    offset = get_offset_for_chat(chat_id)
    tz_label = escape_md(getattr(tz, "zone", str(tz)))
    return (
        "👋 *Привет!* Я бот‑напоминалка встреч.\n\n"
        "*Шаблон:* `ДД.ММ ТИП ЧЧ:ММ ПЕРЕГ НОМЕР`\n"
        "*Пример:* `08.08 МТС 20:40 2в 88634`\n\n"
        "*Текущие настройки:*\n"
        f"• 🌍 TZ: *{tz_label}*\n"
        f"• ⏳ Оффсет: *{offset} мин*\n\n"
        "Отправьте строку встречи — и я всё запланирую ✨"
    )


def show_help_text(_: Any = None) -> str:
    return (
        "❓ *Справка*\n\n"
        "🤖 *Что делает бот*\n"
        "• Создаёт напоминания о встречах по одной строке текста.\n"
        "• Автоматически отправляет сообщение в выбранный чат перед началом.\n"
        "• Позволяет переносить, отменять и повторять напоминания из списка активных задач.\n\n"
        "🆕 *Как создать напоминание*\n"
        "1. Нажмите «🆕 Создать встречу» или просто отправьте строку с данными.\n"
        "2. Используйте формат `ДД.ММ ТИП ЧЧ:ММ ПЕРЕГ НОМЕР` (пример: `08.08 МТС 20:40 2в 88634`).\n"
        "3. В личных сообщениях бот предложит выбрать чат, куда уйдёт напоминание.\n"
        "4. После подтверждения появится карточка с кнопками управления.\n\n"
        "📌 *Где появится напоминание*\n"
        "• В личке можно выбрать любой общий чат или оставить напоминание себе.\n"
        "• В группе напоминание создаётся сразу для текущего чата или выбранной темы.\n"
        "• Чтобы добавить новый чат, пригласите бота и выполните команду `/register` в нужном месте.\n\n"
        "⚙️ *Дополнительные настройки*\n"
        "• В «⚙️ Настройки» можно выбрать таймзону, оффсет и управлять чатами.\n"
        "• Кнопка «📝 Активные» показывает очереди напоминаний (для админов — весь список).\n"
        "• Быстрые кнопки под строкой ввода помогают быстро открыть активные задачи или эту справку."
    )


def create_reminder_hint(chat_id: int) -> str:
    tz = resolve_tz_for_chat(chat_id)
    offset = get_offset_for_chat(chat_id)
    tz_label = escape_md(getattr(tz, "zone", str(tz)))
    return (
        "🆕 *Создать встречу*\n\n"
        "1. Отправьте сообщение формата `ДД.ММ ТИП ЧЧ:ММ ПЕРЕГ НОМЕР`.\n"
        "2. Получите подтверждение с датой и временем напоминания.\n"
        "3. В личных сообщениях можно выбрать чат для отправки.\n\n"
        "_Пример:_ `08.08 МТС 20:40 2в 88634`\n\n"
        f"Напомню за *{offset} мин* до начала. Текущая TZ: *{tz_label}*."
    )


def render_active_text(
    jobs: Iterable[Dict[str, Any]],
    total: int,
    page: int,
    pages_total: int,
    admin: bool,
    *,
    title: str = "📝 Активные",
    empty_message: str = "Пока нет активных напоминаний.",
) -> str:
    """Сформировать HTML со списком задач."""

    jobs_list = list(jobs)
    safe_title = escape(title)
    header = f"<b>{safe_title}</b> ({escape(str(total))}), страница <b>{escape(str(page))}/{escape(str(pages_total))}</b>:"
    lines: list[str] = [header]
    known = get_known_chats()

    for job in jobs_list:
        target_title = job.get("target_title")
        if not target_title:
            chat_id = job.get("target_chat_id")
            target_title = next(
                (c.get("title") for c in known if str(c.get("chat_id")) == str(chat_id)),
                str(chat_id),
            )
            job["target_title"] = target_title

    jobs_list.sort(key=lambda j: (j.get("run_at_utc") or "", j.get("target_title") or ""))

    for index, job in enumerate(jobs_list, start=1):
        tz = pytz.utc
        run_iso = job.get("run_at_utc")
        try:
            dt_utc = datetime.fromisoformat(run_iso)
            if dt_utc.tzinfo is None:
                dt_utc = dt_utc.replace(tzinfo=pytz.utc)
        except Exception:
            dt_utc = None
        target_chat_id = job.get("target_chat_id")
        tz = resolve_tz_for_chat(int(target_chat_id)) if target_chat_id is not None else pytz.utc
        if dt_utc is not None:
            dt_local = dt_utc.astimezone(tz)
            delta = dt_local - datetime.now(tz)
            minutes = int(delta.total_seconds() // 60)
            suffix = (
                f"через {minutes} мин" if minutes >= 0 else f"{abs(minutes)} мин назад"
            )
            when = f"{dt_local:%d.%m %H:%M %Z} ({suffix})"
        else:
            when = run_iso or ""
        title = job.get("target_title") or str(target_chat_id)
        text = job.get("text", "")
        info_lines = [
            "",
            f"<b>{escape(title)}</b>",
            f"{index}) <b>{escape(when)}</b>",
            escape(text),
        ]
        if admin:
            author = job.get("author_username") or job.get("author_id")
            if author:
                author_repr = f"@{escape(str(author))}" if isinstance(author, str) else str(author)
                info_lines.append(f"Создал: {escape(author_repr)}")
        lines.extend(info_lines)

    if len(lines) == 1:
        lines.append("")
        lines.append(escape(empty_message))
    return "\n".join(lines)


def render_admins_text(admins: set[str]) -> str:
    rows = ["👥 Администраторы", ""]
    if admins:
        rows.extend(f"• @{escape_md(name)}" for name in sorted(admins))
    else:
        rows.append("пока нет")
    rows.append("")
    rows.append("Нажмите ➕, чтобы добавить, или ❌ — чтобы удалить.")
    return "\n".join(rows)


def render_panel_text(chat_id: int) -> str:
    tz = resolve_tz_for_chat(chat_id)
    offset = get_offset_for_chat(chat_id)
    jobs = get_jobs_store()
    return (
        "📌 *Панель напоминаний*\n"
        f"Версия: `{VERSION}`\n\n"
        f"🌍 TZ: *{escape_md(getattr(tz, 'zone', str(tz)))}*\n"
        f"⏳ Оффсет: *{offset} мин*\n"
        f"📝 Активных задач: *{len(jobs)}*\n\n"
        "*Формат:* `ДД.ММ ТИП ЧЧ:ММ ПЕРЕГ НОМЕР`\n"
        "_Например:_ `08.08 МТС 20:40 2в 88634`"
    )
