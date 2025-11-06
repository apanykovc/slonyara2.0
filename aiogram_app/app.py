
"""
Aiogram v3 entrypoint for the meeting reminder bot.
Keeps the original logic (parsing, DB, keyboards, texts) and replaces PTB with aiogram+APScheduler.
Run with: python -m telegram_meeting_bot.aiogram_app
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

# Import project modules (do not import anything heavy at top-level in case deps missing during tools runtime)
from telegram_meeting_bot.core import storage, constants
from telegram_meeting_bot.ui.keyboards import (
    main_menu_kb, settings_menu_kb, tz_menu_kb, offset_menu_kb,
    admins_menu_kb, chats_menu_kb, active_kb,
)
from telegram_meeting_bot.ui.texts import (
    menu_text_for, show_help_text, render_active_text,
)

from aiogram.types import InlineKeyboardMarkup as AInlineKeyboardMarkup, InlineKeyboardButton as AInlineKeyboardButton

def mk_markup(kb):
    """
    Convert legacy PTB InlineKeyboardMarkup to aiogram InlineKeyboardMarkup if needed.
    Accepts aiogram markup, PTB-like objects with .inline_keyboard, or dicts.
    """
    if kb is None:
        return None
    if isinstance(kb, AInlineKeyboardMarkup):
        return kb
    # PTB-like
    try:
        rows = []
        inline = getattr(kb, "inline_keyboard", None)
        if inline:
            for row in inline:
                new_row = []
                for btn in row:
                    text = getattr(btn, "text", "")
                    cd = getattr(btn, "callback_data", None)
                    url = getattr(btn, "url", None)
                    if cd is not None:
                        new_row.append(AInlineKeyboardButton(text=text, callback_data=cd))
                    elif url:
                        new_row.append(AInlineKeyboardButton(text=text, url=url))
                if new_row:
                    rows.append(new_row)
            if rows:
                return AInlineKeyboardMarkup(inline_keyboard=rows)
    except Exception:
        pass
    # Dict-like
    try:
        if isinstance(kb, dict) and "inline_keyboard" in kb:
            # assume aiogram-compatible structure
            return AInlineKeyboardMarkup(inline_keyboard=[
                [
                    (AInlineKeyboardButton(text=b.get("text",""), callback_data=b.get("callback_data"))
                     if "callback_data" in b else
                     AInlineKeyboardButton(text=b.get("text",""), url=b.get("url"))
                    )
                    for b in row
                ] for row in kb["inline_keyboard"]
            ])
    except Exception:
        pass
    # Fallback: nothing
    return None

async def answer_with_kb(message, text, kb=None, **kwargs):
    kwargs.pop("reply_markup", None)
    return await message.answer(text, reply_markup=mk_markup(kb), **kwargs)

async def edit_text_with_kb(message, text, kb=None, **kwargs):
    kwargs.pop("reply_markup", None)
    return await message.edit_text(text, reply_markup=mk_markup(kb), **kwargs)

from telegram_meeting_bot.ui.texts import (
    menu_text_for, show_help_text, render_active_text,
)

from aiogram.types import InlineKeyboardMarkup as AInlineKeyboardMarkup, InlineKeyboardButton as AInlineKeyboardButton

def mk_markup(kb):
    """
    Convert legacy PTB InlineKeyboardMarkup to aiogram InlineKeyboardMarkup if needed.
    Accepts aiogram markup, PTB-like objects with .inline_keyboard, or dicts.
    """
    if kb is None:
        return None
    if isinstance(kb, AInlineKeyboardMarkup):
        return kb
    # PTB-like
    try:
        rows = []
        inline = getattr(kb, "inline_keyboard", None)
        if inline:
            for row in inline:
                new_row = []
                for btn in row:
                    text = getattr(btn, "text", "")
                    cd = getattr(btn, "callback_data", None)
                    url = getattr(btn, "url", None)
                    if cd is not None:
                        new_row.append(AInlineKeyboardButton(text=text, callback_data=cd))
                    elif url:
                        new_row.append(AInlineKeyboardButton(text=text, url=url))
                if new_row:
                    rows.append(new_row)
            if rows:
                return AInlineKeyboardMarkup(inline_keyboard=rows)
    except Exception:
        pass
    # Dict-like
    try:
        if isinstance(kb, dict) and "inline_keyboard" in kb:
            # assume aiogram-compatible structure
            return AInlineKeyboardMarkup(inline_keyboard=[
                [
                    (AInlineKeyboardButton(text=b.get("text",""), callback_data=b.get("callback_data"))
                     if "callback_data" in b else
                     AInlineKeyboardButton(text=b.get("text",""), url=b.get("url"))
                    )
                    for b in row
                ] for row in kb["inline_keyboard"]
            ])
    except Exception:
        pass
    # Fallback: nothing
    return None

async def answer_with_kb(message, text, kb=None, **kwargs):
    kwargs.pop("reply_markup", None)
    return await message.answer(text, reply_markup=mk_markup(kb), **kwargs)

async def edit_text_with_kb(message, text, kb=None, **kwargs):
    kwargs.pop("reply_markup", None)
    return await message.edit_text(text, reply_markup=mk_markup(kb), **kwargs)


from telegram_meeting_bot.ui import keyboards as ui_kb, texts as ui_txt
from telegram_meeting_bot.core.parsing import parse_meeting_message

logger = logging.getLogger("reminder-bot.aiogram")

def is_admin_user(user) -> bool:
    try:
        from telegram_meeting_bot.core.storage import get_admin_usernames
        admins = {a.lower().lstrip('@') for a in get_admin_usernames()}
        uname = (user.username or '').lower().lstrip('@')
        return bool(uname) and uname in admins
    except Exception as e:
        return False


router = Router()
scheduler = AsyncIOScheduler()

# ======== Global in-memory helpers (anti-bounce etc.) ========
_last_cb_ts_by_user: dict[int, float] = {}

class ErrorsMiddleware:
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except Exception as e:
            logger.exception("Unhandled error: %s", e)
            try:
                # Try to answer gracefully if it's a message
                if hasattr(event, "answer"):
                    await event.answer("⚠️ Произошла ошибка. Уже разбираюсь.")
            except Exception:
                pass
            return None


# ========= Helpers =========

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

async def _send_safe(bot: Bot, chat_id: int | str, text: str, message_thread_id: Optional[int] = None):
    try:
        await bot.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id)
    except Exception as e:
        logger.exception("send_message failed: %s", e)

# ========= Commands =========

@router.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    # Use existing texts/menus if present, else fallback simple
    try:
        text = menu_text_for(m.chat.id)
    except Exception:
        text = "Привет! Я бот напоминаний. Напиши запрос в формате: ДД.ММ ТИП ЧЧ:ММ ПЕРЕГ НОМЕР"
    try:
        kb = main_menu_kb(constants.is_admin(m.from_user)) if hasattr(constants,'is_admin') else main_menu_kb(False)
    except Exception:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Меню", callback_data="menu:open")]
        ])
    await answer_with_kb(m, text, kb=(main_menu_kb(constants.is_admin(m.from_user)) if hasattr(constants,'is_admin') else main_menu_kb(False)))

@router.message(Command("help"))
async def cmd_help(m: Message):
    try:
        text = show_help_text()
    except Exception:
        text = "Справка по форматам и функциям бота."
    await m.answer(text)

@router.message(Command("menu"))

@router.message(F.text.in_({'📅 Мои встречи', 'Мои встречи'}))
async def btn_my_meetings(m: Message):
    user = m.from_user
    all_jobs = storage.get_jobs_store()
    total = len(all_jobs)
    page_size = getattr(constants, "PAGE_SIZE", 10) or 10
    pages_total = max(1, (total + page_size - 1)//page_size)
    page = 1
    chunk = all_jobs[:page_size]
    admin = is_admin_user(user)
    text = render_active_text(chunk, total, page, pages_total, admin)
    kb = active_kb(chunk, page, pages_total, uid=user.id, is_admin=admin)
    await answer_with_kb(m, text, kb=kb, parse_mode="HTML")

@router.message(F.text.in_({'➕ Создать встречу', 'Создать встречу'}))
async def btn_create(m: Message):
    example = "Пример: 08.08 МТС 20:40 2в 88634"
    await m.answer(f"Отправь строку встречи в формате\n{example}")

@router.message(F.text.in_({'⚙️ Настройки', 'Настройки'}))
async def btn_settings(m: Message):
    await answer_with_kb(m, "Настройки:", kb=settings_menu_kb(), parse_mode="HTML")


@router.message(Command("purge"))
async def cmd_purge(m: Message):
    try:
        is_admin = is_admin_user(m.from_user)
        if not is_admin:
            await m.answer("Только для админов.")
            return
        storage.set_jobs_store([])
        await m.answer("База напоминаний очищена ✅")
    except Exception as e:
        logger.exception("purge failed: %s", e)
        await m.answer("Не удалось очистить БД.")

async def cmd_menu(m: Message):
    try:
        text, kb = menu_text_for(m.chat.id), main_menu_kb(constants.is_admin(m.from_user)) if hasattr(constants,'is_admin') else main_menu_kb(False)
    except Exception:
        text, kb = "Главное меню", None
    await answer_with_kb(m, text, kb=(main_menu_kb(constants.is_admin(m.from_user)) if hasattr(constants,'is_admin') else main_menu_kb(False)))

@router.message(Command("register"))
async def cmd_register(m: Message):
    topic_id = getattr(m, "message_thread_id", None)
    storage.register_chat(m.chat.id, m.chat.title or "", topic_id=topic_id, topic_title=None)
    await m.answer("Чат зарегистрирован ✅")

# ========= Plain text in private =========


@router.message(F.chat.type == "private", F.text & ~F.text.startswith("/"))
async def handle_private_text(m: Message, state: FSMContext):
    text_in = (m.text or "").strip()

    # Admin add/del flows
    data = await state.get_data()
    if data.get("await_admin"):
        uname = text_in.lstrip("@").strip()
        from telegram_meeting_bot.core.storage import add_admin_username
        ok = add_admin_username(uname)
        await state.update_data(await_admin=False)
        await m.answer("Админ добавлен ✅" if ok else "Уже был в списке")
        return
    if data.get("await_admin_del"):
        uname = text_in.lstrip("@").strip()
        from telegram_meeting_bot.core.storage import remove_admin_username
        ok = remove_admin_username(uname)
        await state.update_data(await_admin_del=False)
        await m.answer("Удалён ✅" if ok else "Не найден в списке")
        return

    # AWAIT_TZ flow
    data = await state.get_data()
    if data.get("await_tz"):
        import pytz
        try:
            pytz.timezone(text_in)
        except Exception:
            await m.answer("Некорректная TZ. Пример: `Europe/Moscow`", parse_mode="HTML")
            return
        cfg = storage.get_cfg()
        cfg["tz"] = text_in
        storage.set_cfg(cfg)
        await state.update_data(await_tz=False)
        await m.answer(f"TZ обновлена: `{text_in}`", parse_mode="HTML")
        return

    # Parse meeting line
    from tzlocal import get_localzone
    tz = get_localzone()
    parsed = parse_meeting_message(text_in, tz)
    if not parsed:
        await m.answer("Не распознал формат. Пример: 08.08 МТС 20:40 2в 88634")
        return

    # Choose target chat: default from cfg or "this chat" (0)
    cfg = storage.get_cfg()
    target_chat_id = cfg.get("default_target_chat_id") or 0
    if target_chat_id == 0:
        target_chat_id = m.chat.id

    rec = {
        "job_id": parsed.get("signature") or None,
        "source_chat_id": m.chat.id,
        "target_chat_id": target_chat_id,
        "topic_id": None,
        "text": constants.REMINDER_TEMPLATE.format(
            date=parsed.get("date_str"),
            type=parsed.get("meeting_type"),
            time=parsed.get("time_str"),
            room=parsed.get("room"),
            ticket=parsed.get("ticket"),
        ),
        "run_at_utc": parsed["dt_local"].astimezone(timezone.utc).timestamp(),
    }
    storage.upsert_job_record(rec)
    run_at = datetime.fromtimestamp(rec["run_at_utc"], tz=timezone.utc)
    scheduler.add_job(send_reminder_job, trigger=DateTrigger(run_date=run_at), kwargs={"job_id": rec.get("job_id")}, id=str(rec.get("job_id") or f"ts{int(run_at.timestamp())}"), replace_existing=True)
    await m.answer(f"Напоминание на {parsed['date_str']} {parsed['time_str']} создано ✅")


# ========= Callback handlers (stubs to be wired to your existing callback_data) =========

@router.callback_query(F.data.startswith("menu:"))
async def cb_menu(q: CallbackQuery):
    try:
        text, kb = menu_text_for(m.chat.id), main_menu_kb(constants.is_admin(m.from_user)) if hasattr(constants,'is_admin') else main_menu_kb(False)
    except Exception:
        text, kb = "Главное меню", None
    if q.message:
        await q.message.edit_text(text, reply_markup=kb)
    await q.answer()


@router.callback_query()
async def callbacks_router(q: CallbackQuery, state: FSMContext):
    import time, pytz
    data = q.data or ""
    user = q.from_user
    chat_id = q.message.chat.id if q.message else 0

    # Anti-bounce: ignore same-user callbacks faster than 0.75s
    now_ts = time.monotonic()
    last = _last_cb_ts_by_user.get(user.id, 0.0)
    if now_ts - last < 0.75 and data != getattr(constants, 'CB_NOOP', getattr(constants, 'CB_DISABLED', 'noop')):
        try:
            await q.answer("⏳ Уже выполняю…", cache_time=1)
        except Exception:
            pass
        return
    _last_cb_ts_by_user[user.id] = now_ts

    def safe_edit(text, kb=None):
        return q.message.edit_text(text, reply_markup=kb) if q.message else q.answer()

    # NOOP
    if data == getattr(constants, "CB_NOOP", "noop"):
        try:
            await q.answer("⏳ Уже выполняю…", cache_time=1)
        except Exception:
            pass
        return

    # MENU / HELP / SETTINGS
    if data == constants.CB_MENU:
        await safe_edit(menu_text_for(chat_id), main_menu_kb(is_admin_user(user)))
        await q.answer()
        return

    if data == constants.CB_HELP:
        await safe_edit(show_help_text())
        await q.answer()
        return

    if data == constants.CB_SETTINGS:
        # Show settings keyboard (TZ / offset / chats / admins)
        try:
            await safe_edit(show_help_text(), settings_menu_kb())
        except Exception:
            await q.message.answer(show_help_text(), reply_markup=settings_menu_kb())
        await q.answer()
        return

    # Active list & paging
    if data == constants.CB_ACTIVE or data.startswith(constants.CB_ACTIVE_PAGE + ":"):
        import math
        page = 1
        if ":" in data:
            try:
                page = int(data.split(":")[1])
            except Exception:
                page = 1
        all_jobs = storage.get_jobs_store()
        total = len(all_jobs)
        page_size = getattr(constants, "PAGE_SIZE", 10) or 10
        pages_total = max(1, math.ceil(total / page_size))
        page = max(1, min(page, pages_total))
        start = (page - 1) * page_size
        chunk = all_jobs[start:start+page_size]
        is_admin = is_admin_user(user)
        text = render_active_text(chunk, total, page, pages_total, is_admin)
        kb = active_kb(chunk, page, pages_total, uid=user.id, is_admin=is_admin)
        try:
            await edit_text_with_kb(q.message, text, kb=kb, parse_mode="HTML")
        except Exception:
            await answer_with_kb(q.message, text, kb=kb, parse_mode="HTML")
        await q.answer()
        return


    # TZ flows
    if data == constants.CB_SET_TZ:
        await edit_text_with_kb(q.message, "Выбери вариант TZ или введи свою:", kb=tz_menu_kb(), parse_mode="HTML")
        await q.answer()
        return

    if data == constants.CB_SET_TZ_LOCAL:
        await state.update_data(await_tz=True)
        await q.message.edit_text("Введи TZ (например, `Europe/Moscow`)", parse_mode="HTML")
        await q.answer()
        return

    if data in (getattr(constants, "CB_SET_TZ_MOSCOW", "set_tz_eu_msk"), getattr(constants, "CB_SET_TZ_CHICAGO", "set_tz_us_chi")):
        tz_map = {
            getattr(constants, "CB_SET_TZ_MOSCOW", "set_tz_eu_msk"): "Europe/Moscow",
            getattr(constants, "CB_SET_TZ_CHICAGO", "set_tz_us_chi"): "America/Chicago",
        }
        tzname = tz_map.get(data)
        cfg = storage.get_cfg()
        cfg["tz"] = tzname
        storage.set_cfg(cfg)
        await q.answer("TZ обновлена")
        return

    # OFFSETS
    if data == constants.CB_SET_OFFSET:
        cfg = storage.get_cfg()
        off = int(cfg.get("offset_minutes", 0))
        await edit_text_with_kb(q.message, f"Текущий offset: {off} мин", kb=offset_menu_kb(off), parse_mode="HTML")
        await q.answer()
        return

    if data in (constants.CB_OFF_INC, constants.CB_OFF_DEC):
        cfg = storage.get_cfg()
        off = int(cfg.get("offset_minutes", 0))
        off += 10 if data == constants.CB_OFF_INC else -10
        storage.set_cfg({"offset_minutes": off})
        await q.message.edit_reply_markup(reply_markup=mk_markup(offset_menu_kb(off)))
        await q.answer("Ок")
        return

    # CHATS mgmt
    if data == constants.CB_CHATS:
        chats = storage.get_chats()
        await edit_text_with_kb(q.message, "Зарегистрированные чаты/темы:", kb=chats_menu_kb(chats), parse_mode="HTML")
        await q.answer()
        return

    if data.startswith(constants.CB_CHAT_DEL + ":"):
        _, raw = data.split(":", 1)
        try:
            cid, tid = raw.split(",", 1)
            storage.unregister_chat(int(cid), int(tid) if tid != "0" else None)
        except Exception:
            pass
        chats = storage.get_chats()
        await edit_text_with_kb(q.message, "Зарегистрированные чаты/темы:", kb=chats_menu_kb(chats), parse_mode="HTML")
        await q.answer("Удалено")
        return

    # ADMINS list/add/del
    if data == getattr(constants, "CB_ADMINS", "admins"):
        from telegram_meeting_bot.core.constants import ADMIN_USERNAMES
        await edit_text_with_kb(q.message, "Администраторы:", kb=admins_menu_kb(ADMIN_USERNAMES), parse_mode="HTML")
        await q.answer()
        return

    if data == getattr(constants, "CB_ADMIN_ADD", "admin_add"):
        await state.update_data(await_admin=True)
        await q.message.edit_text("Введи @username для добавления в админы")
        await q.answer()
        return

    if data == getattr(constants, "CB_ADMIN_DEL", "admin_del"):
        await state.update_data(await_admin_del=True)
        await q.message.edit_text("Введи @username для удаления из админов")
        await q.answer()
        return

    # Actions panel open/close
    if data.startswith(constants.CB_ACTIONS + ":"):
        parts = data.split(":")
        jid = parts[1] if len(parts) >= 2 else None
        if len(parts) >= 3 and parts[2] == "close":
            # back to page 1
            all_jobs = storage.get_jobs_store()
            total = len(all_jobs)
            page_size = getattr(constants, "PAGE_SIZE", 10) or 10
            pages_total = max(1, (total + page_size - 1)//page_size)
            page = 1
            chunk = all_jobs[:page_size]
            is_admin = is_admin_user(user)
            text = render_active_text(chunk, total, page, pages_total, is_admin)
            kb = active_kb(chunk, page, pages_total, uid=user.id, is_admin=is_admin)
            await edit_text_with_kb(q.message, text, kb=kb, parse_mode="HTML")
            await q.answer()
            return
        rec = storage.get_job_record(jid)
        if not rec:
            await q.answer("Не найдено")
            return
        label = rec.get("text", "задача")
        from telegram_meeting_bot.ui.keyboards import actions_kb
        kb = actions_kb(jid, is_admin=is_admin_user(user))
        await edit_text_with_kb(q.message, f"⚙️ Действия для «{label}»", kb=kb, parse_mode="HTML")
        await q.answer()
        return

    # Send now / Cancel / Shift
    if data.startswith(constants.CB_SENDNOW + ":"):
        jid = data.split(":")[1]
        await send_reminder_job(job_id=jid)
        await q.answer("Отправлено")
        return

    if data.startswith(constants.CB_CANCEL + ":"):
        jid = data.split(":")[1]
        storage.remove_job_record(jid)
        try:
            scheduler.remove_job(jid)
        except Exception:
            pass
        # refresh current view
        all_jobs = storage.get_jobs_store()
        total = len(all_jobs)
        page_size = getattr(constants, "PAGE_SIZE", 10) or 10
        pages_total = max(1, (total + page_size - 1)//page_size)
        page = 1
        chunk = all_jobs[:page_size]
        is_admin = is_admin_user(user)
        text = render_active_text(chunk, total, page, pages_total, is_admin)
        kb = active_kb(chunk, page, pages_total, uid=user.id, is_admin=is_admin)
        await edit_text_with_kb(q.message, text, kb=kb, parse_mode="HTML")
        await q.answer("Отменено")
        return

    if data.startswith(constants.CB_SHIFT + ":"):
        parts = data.split(":")
        jid = parts[1]
        minutes = int(parts[2]) if len(parts) >= 3 else 5
        rec = storage.get_job_record(jid)
        if not rec:
            await q.answer("Не найдено")
            return
        new_ts = _to_ts(rec.get("run_at_utc", 0)) + minutes * 60
        rec["run_at_utc"] = new_ts
        storage.upsert_job_record(rec)
        run_at = datetime.fromtimestamp(new_ts, tz=timezone.utc)
        scheduler.add_job(send_reminder_job, trigger=DateTrigger(run_date=run_at), kwargs={"job_id": jid}, id=str(jid), replace_existing=True)
        await q.answer(f"Сдвинул на +{minutes} мин")
        return
# ========= Jobs =========

def _to_ts(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        v = v.strip()
        try:
            return int(v)
        except Exception:
            try:
                return int(float(v.replace(',', '.')))
            except Exception:
                return 0
    return 0
# ========= Jobs =========

async def send_reminder_job(job_id: str | None = None, **kwargs):
    """Execute reminder: load by job_id from storage and send."""
    # Lazy import bot into job context:
    bot: Bot = send_reminder_job.bot  # type: ignore[attr-defined]
    if not job_id:
        return
    rec = storage.get_job_record(job_id)
    if not rec:
        return
    await _send_safe(bot, rec["target_chat_id"], rec["text"], message_thread_id=rec.get("topic_id"))
    # Remove after send if it's one-shot
    storage.remove_job_record(job_id)

def restore_jobs(sched: AsyncIOScheduler):
    """Restore APScheduler jobs from storage at startup (with simple catch-up)."""
    now = _utc_now()
    for r in storage.get_jobs_store():
        run_at = datetime.fromtimestamp(_to_ts(r.get("run_at_utc", 0)), tz=timezone.utc)
        if run_at <= now:
            # catch-up: send immediately in background
            asyncio.create_task(send_reminder_job(job_id=r.get("job_id")))
        else:
            sched.add_job(send_reminder_job, trigger=DateTrigger(run_date=run_at), kwargs={"job_id": r.get("job_id")}, id=str(r.get("job_id")), replace_existing=True)

# ========= Lifecycle =========

async def on_startup(bot: Bot):
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Приветствие и меню"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="help", description="Подсказка"),
            BotCommand(command="register", description="Зарегистрировать чат/тему"),
        ])
    except Exception as e:
        logger.warning("set_my_commands failed: %s", e)
    send_reminder_job.bot = bot  # provide bot to job
    scheduler.start()
    restore_jobs(scheduler)
    # ensure default admin
    try:
        from telegram_meeting_bot.core.storage import add_admin_username
        add_admin_username('panykovc')
    except Exception as e:
        logger.warning('cannot add default admin: %s', e)
    logger.info("startup complete")

async def on_shutdown():
    scheduler.shutdown(wait=False)
    logger.info("shutdown complete")

async def main():
    logging.basicConfig(level=logging.INFO)
    cfg = storage.get_cfg()
    token = (cfg.get("token") if isinstance(cfg, dict) else None) or constants.BOT_TOKEN  # fallback if defined
    if not token:
        raise SystemExit("Token not configured. Put it into data/config.json as {'token':'...'}")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(ErrorsMiddleware())
    dp.callback_query.middleware(ErrorsMiddleware())
    dp.startup.register(on_startup)
    dp.startup.register(lambda: on_startup(bot))
    dp.shutdown.register(on_shutdown)
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
