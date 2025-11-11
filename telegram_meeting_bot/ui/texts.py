from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
from html import escape
from typing import Any, Dict, Iterable, Sequence

import pytz

from ..core.constants import PAGE_SIZE, RR_DAILY, RR_ONCE, RR_WEEKLY, VERSION
from ..core.logs import LogFileInfo, LogFileView
from ..core.storage import (
    get_jobs_store,
    get_known_chats,
    get_offset_for_chat,
    normalize_offset,
    resolve_tz_for_chat,
)
from ..core import logs as log_utils


MOSCOW_TZ = pytz.timezone("Europe/Moscow")
APP_LOG_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?P<rest>.*)$")
APP_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_BODY_CHAR_LIMIT = 3500


def _parse_utc_naive(timestamp: str) -> datetime | None:
    try:
        dt = datetime.strptime(timestamp, APP_TS_FORMAT)
    except ValueError:
        return None
    return pytz.utc.localize(dt)


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    ts = value
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(pytz.utc)


def _format_app_log(line: str) -> str:
    match = APP_LOG_RE.match(line)
    if not match:
        return line
    dt = _parse_utc_naive(match.group("ts"))
    if dt is None:
        return line
    dt_local = dt.astimezone(MOSCOW_TZ)
    return f"{dt_local.strftime(APP_TS_FORMAT)} MSK{match.group('rest')}"


def _format_json_log(line: str) -> str:
    try:
        payload = json.loads(line)
    except (TypeError, ValueError):
        return line
    ts = payload.get("ts") or payload.get("timestamp")
    dt = _parse_iso_timestamp(ts)
    if dt is not None:
        payload["ts_msk"] = dt.astimezone(MOSCOW_TZ).strftime(APP_TS_FORMAT)
    return json.dumps(payload, ensure_ascii=False)


def _format_log_entry(log_type: str, entry: Sequence[str]) -> list[str]:
    if not entry:
        return []
    key = log_type.lower()
    head = entry[0]
    if key == log_utils.LOG_TYPE_APP:
        head = _format_app_log(head)
    elif key in {log_utils.LOG_TYPE_AUDIT, log_utils.LOG_TYPE_ERROR}:
        head = _format_json_log(head)
    return [head, *entry[1:]]


def _format_size(value: int) -> str:
    units = ["Б", "КБ", "МБ", "ГБ"]
    size = float(max(value, 0))
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "Б":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} ГБ"


def _trim_entries_for_display(entries: Sequence[str], limit: int) -> tuple[list[str], bool]:
    selected = list(entries)
    truncated = False
    if not selected:
        return selected, truncated
    total_length = sum(len(item) for item in selected) + max(len(selected) - 1, 0) * 2
    while selected and total_length > limit:
        selected.pop(0)
        truncated = True
        total_length = sum(len(item) for item in selected) + max(len(selected) - 1, 0) * 2
    return selected, truncated


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


def render_log_file_list(log_type: str, files: Sequence[LogFileInfo]) -> str:
    labels = {
        log_utils.LOG_TYPE_APP: "📗 <b>App</b>",
        log_utils.LOG_TYPE_AUDIT: "🧾 <b>Audit</b>",
        log_utils.LOG_TYPE_ERROR: "❌ <b>Error</b>",
    }
    descriptions = {
        log_utils.LOG_TYPE_APP: "Рабочие события бота и планировщика.",
        log_utils.LOG_TYPE_AUDIT: "История действий пользователей и изменений напоминаний.",
        log_utils.LOG_TYPE_ERROR: "Ошибки, предупреждения и служебные исключения.",
    }
    kind = log_type.lower()
    title = labels.get(kind, f"📜 <b>{escape(kind.title())}</b>")
    header_lines = [f"{title} — файлы журнала"]
    description = descriptions.get(kind)
    if description:
        header_lines.append(f"<i>{escape(description)}</i>")
    header = "\n".join(header_lines)
    if not files:
        body = "<i>Файлы не найдены.</i>"
    else:
        lines = []
        for info in files:
            label = info.label or info.name
            size_text = _format_size(info.size_bytes)
            if info.modified_at:
                modified = info.modified_at.astimezone(MOSCOW_TZ)
                meta = f"{modified:%d.%m %H:%M %Z}, {size_text}"
            else:
                meta = size_text
            lines.append(f"• <code>{escape(label)}</code> — {escape(meta)}")
        body = "\n".join(lines)
    hint = "Выберите файл, чтобы посмотреть записи, или скачайте архив для полной истории."
    return f"{header}\n\n{body}\n\n<i>{escape(hint)}</i>"


def render_log_file(log_type: str, info: LogFileInfo, view: LogFileView) -> str:
    labels = {
        log_utils.LOG_TYPE_APP: "📗 <b>App</b>",
        log_utils.LOG_TYPE_AUDIT: "🧾 <b>Audit</b>",
        log_utils.LOG_TYPE_ERROR: "❌ <b>Error</b>",
    }
    kind = log_type.lower()
    title = labels.get(kind, f"📜 <b>{escape(kind.title())}</b>")
    file_label = info.label or info.name
    header_lines = [f"{title} — файл <code>{escape(file_label)}</code>"]
    meta_parts: list[str] = []
    if info.modified_at:
        modified = info.modified_at.astimezone(MOSCOW_TZ)
        meta_parts.append(f"Обновлён {modified:%d.%m %H:%M %Z}")
    meta_parts.append(f"Размер {_format_size(info.size_bytes)}")
    if view.total:
        meta_parts.append(f"Записей: {view.total}")
    if meta_parts:
        header_lines.append(f"<i>{escape('; '.join(meta_parts))}</i>")
    header = "\n".join(header_lines)
    if not view.entries:
        body = "<i>Файл пуст.</i>"
        shown = 0
        truncated = False
    else:
        formatted_entries = [
            "\n".join(escape(line) for line in _format_log_entry(kind, entry))
            for entry in view.entries
        ]
        display_entries, cut = _trim_entries_for_display(formatted_entries, LOG_BODY_CHAR_LIMIT)
        truncated = cut or view.truncated
        shown = len(display_entries)
        snippet = "\n\n".join(display_entries)
        body = f"<pre>{snippet}</pre>"
    footer_lines: list[str] = []
    if truncated and view.total:
        footer_lines.append(
            f"Показаны последние {shown} из {view.total} записей. Полный файл можно скачать из архива."
        )
    else:
        footer_lines.append("Записи отображаются блоками так, как они были сохранены в логе.")
    footer = "\n".join(escape(line) for line in footer_lines)
    return f"{header}\n\n{body}\n\n<i>{footer}</i>"


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
