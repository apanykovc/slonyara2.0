from typing import Dict, Any
from datetime import datetime
from html import escape

import pytz
from telegram import Update

from ..core.constants import VERSION, RR_ONCE, RR_DAILY, RR_WEEKLY
from ..core.storage import (
    resolve_tz_for_chat,
    get_offset_for_chat,
    get_jobs_store,
    get_known_chats,
)


def menu_text_for(chat_id: int) -> str:
    tz = resolve_tz_for_chat(chat_id)
    offset = get_offset_for_chat(chat_id)
    tz_label = escape_md(tz.zone)
    return (
        "👋 *Привет!* Я бот‑напоминалка встреч.\n\n"
        "*Шаблон:* `ДД.ММ ТИП ЧЧ:ММ ПЕРЕГ НОМЕР`\n"
        "*Пример:* `08.08 МТС 20:40 2в 88634`\n\n"
        "*Текущие настройки:*\n"
        f"• 🌍 TZ: *{tz_label}*\n"
        f"• ⏳ Оффсет: *{offset} мин*\n\n"
        "Отправьте строку встречи — и я всё запланирую ✨"
    )


def show_help_text(_update: Update | None = None) -> str:
    return (
        "❓ *Справка*\n\n"
        "*Формат:* `ДД.ММ ТИП ЧЧ:ММ ПЕРЕГ НОМЕР`\n"
        "*Пример:* `08.08 МТС 20:40 2в 88634`\n\n"
        "*Куда придёт напоминание*\n"
        "• В личке бот ищет общие группы и предлагает выбрать одну из них\n"
        "• Если общих чатов нет, напоминание придёт в этот диалог\n"
        "• Админ может добавить чат командой `/register` прямо в группе\n"
        "• Поддерживаются: `t.me/c/123/45`, `t.me/c/123`, `web.telegram.org/k/#-100...`, `@PublicGroup`, `-100...`, `0`\n"
        "_Инвайт‑ссылки `t.me/+...` не поддерживаются — добавьте бота в чат и используйте `t.me/c` или `@username`._\n\n"
        "*Действия над задачей:* нажмите ⚙️ рядом с записью — можно *отменить*, *отправить сейчас*, *+5*, *+10*, *повторы* (разово/ежедневно/еженедельно)."
    )


def format_job_line(
    j: Dict[str, Any],
    tz_for_chat: pytz.BaseTzInfo,
    include_text: bool = True,
    include_icon: bool = True,
) -> str:
    """Вернуть строку с временем и при желании текстом напоминания."""
    run_at_utc = j.get("run_at_utc"); text = j.get("text", "")
    rrule = j.get("rrule", RR_ONCE)
    ico_map = {"once": "•", "daily": "📅", "weekly": "🗓️"}
    rr_ico = ico_map.get(rrule, "•") if include_icon else ""
    try:
        dt_utc = datetime.fromisoformat(run_at_utc)
        dt_loc = dt_utc.astimezone(tz_for_chat)
        delta = dt_loc - datetime.now(tz_for_chat)
        mins = int(delta.total_seconds() // 60)
        suffix = f"через {mins} мин" if mins >= 0 else f"{abs(mins)} мин назад"
        when = f"{dt_loc.strftime('%d.%m %H:%M %Z')} ({suffix})"
    except Exception:
        when = run_at_utc or ""
    parts = []
    if rr_ico:
        parts.append(rr_ico)
    parts.append(when)
    line = " ".join(parts)
    if include_text:
        return f"{line}\n{text}"
    return line


def render_panel_text(chat_id: int) -> str:
    tz = resolve_tz_for_chat(chat_id)
    offset = get_offset_for_chat(chat_id)
    jobs = get_jobs_store()
    tz_label = escape_md(tz.zone)
    return (
        "📌 *Панель напоминаний*\n"
        f"Версия: `{VERSION}`\n\n"
        f"🌍 TZ: *{tz_label}*   ⏳ Оффсет: *{offset} мин*\n"
        f"📝 Активных задач: *{len(jobs)}*\n\n"
        "*Формат:* `ДД.ММ ТИП ЧЧ:ММ ПЕРЕГ НОМЕР`\n"
        "_Например:_ `08.08 МТС 20:40 2в 88634`"
    )


def escape_md(text: str) -> str:
    """Экранировать спецсимволы Markdown в динамических данных."""

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


def render_admins_text(admins: set[str]) -> str:
    lines = ["👥 Администраторы", ""]
    if admins:
        lines.extend(f"• @{escape_md(a)}" for a in sorted(admins))
    else:
        lines.append("пока нет")
    lines.append("")
    lines.append("Нажмите ➕, чтобы добавить, или ❌ для удаления.")
    return "\n".join(lines)


def render_active_text(
    jobs: list[Dict[str, Any]],
    total: int,
    page: int,
    pages_total: int,
    admin: bool,
) -> str:
    """Сформировать текст для списка активных напоминаний (HTML)."""

    lines = [
        f"📝 <b>Активные</b> ({escape(str(total))}), страница <b>{escape(str(page))}/{escape(str(pages_total))}</b>:"
    ]
    WEEKDAYS = [
        "Понедельник",
        "Вторник",
        "Среда",
        "Четверг",
        "Пятница",
        "Суббота",
        "Воскресенье",
    ]
    from collections import defaultdict

    known = get_known_chats()
    grouped: dict[str, list[Dict[str, Any]]] = defaultdict(list)
    dt_map: dict[str, datetime] = {}

    for j in jobs:
        tgt = j.get("target_chat_id")
        # Сохраняем название чата для дальнейшего использования в кнопках
        j.setdefault(
            "target_title",
            next((c.get("title") for c in known if str(c.get("chat_id")) == str(tgt)), str(tgt)),
        )
        tz_local = resolve_tz_for_chat(tgt)
        run_iso = j.get("run_at_utc", "")
        try:
            dt_loc = datetime.fromisoformat(run_iso).astimezone(tz_local)
            date_key = dt_loc.strftime("%Y-%m-%d")
            dt_map[date_key] = dt_loc
        except Exception:
            date_key = run_iso
            dt_loc = None
        j["_dt_loc"] = dt_loc
        j["_tz"] = tz_local
        grouped[date_key].append(j)

    for date_key in sorted(grouped.keys()):
        dt_loc = dt_map.get(date_key)
        if dt_loc is not None:
            date_label = f"{dt_loc:%d.%m} | {WEEKDAYS[dt_loc.weekday()]}"
        else:
            date_label = date_key
        lines.append("")
        lines.append(f"<b>{escape(date_label)}:</b>")
        lines.append("")
        day_jobs = grouped[date_key]
        day_jobs.sort(key=lambda j: (j.get("_dt_loc") or datetime.max, j.get("target_title")))
        for idx, j in enumerate(day_jobs, 1):
            tz_local = j.get("_tz")
            dt_loc = j.get("_dt_loc")
            run_iso = j.get("run_at_utc", "")
            created_iso = j.get("created_at_utc")
            title = j.get("target_title") or str(j.get("target_chat_id"))
            if dt_loc is not None:
                delta = dt_loc - datetime.now(tz_local)
                mins = int(delta.total_seconds() // 60)
                suffix = (
                    f"через {mins} мин" if mins >= 0 else f"{abs(mins)} мин назад"
                )
                run_part = dt_loc.strftime("%H:%M %Z")
            else:
                suffix = ""
                run_part = run_iso
            try:
                created_local = datetime.fromisoformat(created_iso).astimezone(tz_local)
                created_part = created_local.strftime("%d.%m в %H:%M")
            except Exception:
                created_part = created_iso or ""
            created_display = escape(created_part) if created_part else "—"
            run_display = escape(run_part)
            suffix_display = escape(suffix) if suffix else ""
            lines.append(f"<b>{escape(title)}</b>:")
            lines.append("")
            line = f"{idx}) {created_display} | <b>Напоминалка на {run_display}"
            if suffix_display:
                line += f" ({suffix_display})"
            line += "</b>"
            if admin:
                author = j.get("author_username") or j.get("author_id")
                if author is not None:
                    if isinstance(author, str):
                        clean = author[1:] if str(author).startswith("@") else str(author)
                        author_display = f"@{escape(clean)}"
                    else:
                        author_display = escape(str(author))
                    line += f" была создана {author_display}"
            lines.append(line)
            lines.append("")
        lines.append(f"<b>Всего ВКС: {len(day_jobs)}</b>")
        lines.append("_" * 70)

    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)
