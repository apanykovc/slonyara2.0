from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from typing import Any, Dict, Iterable

import pytz

from ..core.constants import PAGE_SIZE, RR_DAILY, RR_ONCE, RR_WEEKLY, VERSION
from ..core.storage import (
    get_jobs_store,
    get_known_chats,
    get_offset_for_chat,
    normalize_offset,
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
        offset_minutes = normalize_offset(job.get("offset_minutes"), fallback=None)
        if offset_minutes == 0 and job.get("offset_minutes") is None:
            try:
                cfg_id = int(target_chat_id)
            except (TypeError, ValueError):
                cfg_id = None
            if cfg_id is not None:
                offset_minutes = get_offset_for_chat(cfg_id)

        meeting_local = None
        if dt_utc is not None:
            dt_local = dt_utc.astimezone(tz)
            delta = dt_local - datetime.now(tz)
            minutes = int(delta.total_seconds() // 60)
            suffix = (
                f"через {minutes} мин" if minutes >= 0 else f"{abs(minutes)} мин назад"
            )
            extra = ""
            if offset_minutes:
                meeting_local = dt_local + timedelta(minutes=offset_minutes)
                extra = f"; напоминание за {offset_minutes} мин до встречи"
            when = f"{dt_local:%d.%m %H:%M %Z} ({suffix}{extra})"
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
        if meeting_local is not None:
            info_lines.append(f"Встреча: {meeting_local:%d.%m %H:%M %Z}")
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


def render_archive_text(
    items: Iterable[Dict[str, Any]],
    total: int,
    page: int,
    pages_total: int,
    *,
    title: str = "📦 Архив",
    empty_message: str = "Архив пуст.",
    page_size: int = PAGE_SIZE,
) -> str:
    """Сформировать HTML для списка архивных напоминаний."""

    entries = list(items)
    safe_title = escape(title)
    header = (
        f"<b>{safe_title}</b> ({escape(str(total))}), страница "
        f"<b>{escape(str(page))}/{escape(str(pages_total))}</b>:"
    )
    lines: list[str] = [header]
    if not entries:
        lines.append("")
        lines.append(escape(empty_message))
        return "\n".join(lines)

    known = get_known_chats()
    reason_labels = {
        "completed": "✅ Завершено",
        "manual_cancel": "❌ Отменено вручную",
        "chat_removed": "🚫 Чат недоступен",
        "bot_removed": "🚫 Бот исключён",
        "chat_unregistered": "🗑️ Чат удалён из настроек",
    }

    def _parse_iso(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.utc)
        return dt

    index_offset = max(page - 1, 0) * max(page_size, 1)

    for index, entry in enumerate(entries, start=1 + index_offset):
        target_title = entry.get("target_title")
        chat_id = entry.get("target_chat_id")
        if not target_title:
            target_title = next(
                (c.get("title") for c in known if str(c.get("chat_id")) == str(chat_id)),
                str(chat_id),
            )

        tz = pytz.utc
        tz_chat_id: int | None = None
        if isinstance(chat_id, int):
            tz_chat_id = chat_id
        else:
            try:
                tz_chat_id = int(chat_id)
            except (TypeError, ValueError):
                tz_chat_id = None
        if tz_chat_id is not None:
            try:
                tz = resolve_tz_for_chat(tz_chat_id)
            except Exception:
                tz = pytz.utc

        archived_dt = _parse_iso(entry.get("archived_at_utc") or entry.get("archived_at"))
        archived_text = (
            archived_dt.astimezone(tz).strftime("%d.%m %H:%M %Z")
            if archived_dt is not None
            else entry.get("archived_at_utc") or ""
        )

        run_dt = _parse_iso(entry.get("run_at_utc"))
        run_text = (
            run_dt.astimezone(tz).strftime("%d.%m %H:%M %Z")
            if run_dt is not None
            else entry.get("run_at_utc") or ""
        )

        topic_title = entry.get("topic_title")
        if not topic_title:
            rec_topic = entry.get("topic_id")
            if rec_topic is not None:
                topic_title = next(
                    (
                        c.get("topic_title")
                        for c in known
                        if str(c.get("chat_id")) == str(chat_id)
                        and int(c.get("topic_id", 0) or 0) == int(rec_topic or 0)
                    ),
                    None,
                )

        text = entry.get("text") or ""
        reason = entry.get("archive_reason") or "completed"
        reason_label = reason_labels.get(reason, "📦 Архивировано")
        removed_by = entry.get("removed_by") if isinstance(entry.get("removed_by"), dict) else None
        remover_text = ""
        if isinstance(removed_by, dict):
            username = removed_by.get("username")
            full_name = removed_by.get("full_name")
            user_id = removed_by.get("user_id")
            if username:
                remover_text = f"@{username}"
            elif full_name:
                remover_text = str(full_name)
            elif user_id:
                remover_text = str(user_id)
            if user_id and remover_text and str(user_id) not in remover_text:
                remover_text = f"{remover_text} (ID: {user_id})"

        lines.extend(
            [
                "",
                f"{index}) <b>{escape(str(target_title))}</b>",
                escape(text),
            ]
        )
        if topic_title:
            lines.append(f"Тема: {escape(str(topic_title))}")
        if run_text:
            lines.append(f"Напоминание планировалось на {escape(str(run_text))}")
        if archived_text:
            lines.append(f"{escape(reason_label)}: {escape(str(archived_text))}")
        if remover_text:
            lines.append(f"Инициатор: {escape(remover_text)}")

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
