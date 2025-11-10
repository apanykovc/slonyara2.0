from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

import pytz
from aiohttp import ClientError
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardMarkup, Message, User
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from tzlocal import get_localzone_name

from telegram_meeting_bot.core import constants, storage
from telegram_meeting_bot.core.audit import audit_log
from telegram_meeting_bot.core.logging_setup import setup_logging
from telegram_meeting_bot.core.parsing import parse_meeting_message
from telegram_meeting_bot.ui import keyboards as ui_kb, texts as ui_txt


CB_NOOP = getattr(constants, "CB_NOOP", None) or getattr(constants, "CB_DISABLED", "noop")


logger = setup_logging()

router = Router()
scheduler = AsyncIOScheduler(timezone=timezone.utc)

STATE_AWAIT_TZ = "await_tz"
STATE_AWAIT_ADMIN_ADD = "await_admin"
STATE_AWAIT_ADMIN_DEL = "await_admin_del"
STATE_PENDING = "pending_reminders"
STATE_REPLY_MENU_SHOWN = "reply_menu_shown"
STATE_FORCE_PICK = "force_pick"
STATE_LAST_TARGET = "last_target"


REPLY_MENU_ACTIONS = {
    "menu": {"меню"},
    "create": {"➕ создать встречу", "+ создать встречу", "🆕 создать встречу", "создать встречу"},
    "my": {"📂 мои встречи", "мои встречи"},
    "active": {"📝 активные", "активные"},
    "settings": {"⚙️ настройки", "настройки"},
    "help": {"❓ справка", "справка"},
}

REPLY_MENU_ALIASES = {
    alias.casefold(): action
    for action, aliases in REPLY_MENU_ACTIONS.items()
    for alias in aliases
}


class ErrorsMiddleware:
    async def __call__(self, handler, event, data):  # type: ignore[override]
        try:
            return await handler(event, data)
        except Exception as exc:  # pragma: no cover - defensive layer
            logger.exception("Unhandled error", exc_info=exc)
            message = getattr(event, "message", None)
            if isinstance(message, Message):
                with suppress(Exception):
                    await _answer_safe(message, "⚠️ Что-то пошло не так. Уже разбираюсь.")
            return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_admin(user: Optional[User]) -> bool:
    if user is None:
        return False
    if user.id in constants.ADMIN_IDS:
        return True
    username = (user.username or "").lower().lstrip("@")
    if not username:
        return False
    if username in constants.ADMIN_USERNAMES:
        return True
    owners = getattr(constants, "OWNER_USERNAMES", {"panykovc"})
    return username in owners or username == "panykovc"


def _is_owner(user: Optional[User]) -> bool:
    if user is None:
        return False
    username = (user.username or "").lower().lstrip("@")
    owners = getattr(constants, "OWNER_USERNAMES", {"panykovc"})
    return username in owners or _is_admin(user)


def _can_manage_settings(user: Optional[User], chat: Optional[Any]) -> bool:
    chat_type = getattr(chat, "type", None)
    if chat_type == "private":
        return True
    return _is_admin(user)


def _paginate_jobs(
    page: int,
    page_size: int,
    *,
    predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> tuple[list[Dict[str, Any]], int, int]:
    jobs_all = list(storage.get_jobs_store())
    if predicate is not None:
        jobs_filtered = [job for job in jobs_all if predicate(job)]
    else:
        jobs_filtered = jobs_all

    def sort_key(job: Dict[str, Any]) -> tuple[float, str, str, str]:
        run_iso = job.get("run_at_utc")
        timestamp = float("inf")
        if isinstance(run_iso, str) and run_iso:
            try:
                run_at = datetime.fromisoformat(run_iso)
            except ValueError:
                run_at = None
            if run_at is not None:
                if run_at.tzinfo is None:
                    run_at = run_at.replace(tzinfo=timezone.utc)
                timestamp = run_at.astimezone(timezone.utc).timestamp()
        title = job.get("target_title") or str(job.get("target_chat_id") or "")
        text = job.get("text") or ""
        job_id = job.get("job_id") or ""
        return timestamp, title, text, job_id

    jobs_filtered.sort(key=sort_key)
    total = len(jobs_filtered)
    pages_total = max(1, (total + page_size - 1) // page_size) if total else 1
    page = max(1, min(page, pages_total))
    start = (page - 1) * page_size
    chunk = jobs_filtered[start : start + page_size]
    return chunk, total, pages_total


def _schedule_job(job_id: str, run_at: datetime) -> None:
    scheduler.add_job(
        send_reminder_job,
        trigger=DateTrigger(run_date=run_at.astimezone(timezone.utc)),
        id=job_id,
        kwargs={"job_id": job_id},
        replace_existing=True,
    )


_RETRYABLE_TELEGRAM_ERRORS = (
    TelegramNetworkError,
    ClientError,
    asyncio.TimeoutError,
    OSError,
)


async def _telegram_call(
    action: Callable[[], Awaitable[Any]],
    *,
    description: str,
    swallow_bad_request: bool = False,
    retries: int = 3,
    base_delay: float = 0.75,
    bad_request_handler: Optional[Callable[[TelegramBadRequest], None]] = None,
    raise_on_failure: bool = False,
    on_give_up: Optional[Callable[[Exception], Awaitable[None]]] = None,
) -> Any:
    """Execute Telegram API call with retries and detailed logging."""

    for attempt in range(1, max(1, retries) + 1):
        try:
            return await action()
        except TelegramRetryAfter as exc:
            wait = float(getattr(exc, "retry_after", base_delay) or base_delay)
            logger.warning(
                "%s rate limited, sleeping for %.2fs (attempt %s/%s)",
                description,
                wait,
                attempt,
                retries,
            )
            await asyncio.sleep(wait)
        except TelegramBadRequest as exc:
            if bad_request_handler:
                try:
                    bad_request_handler(exc)
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception("%s bad request handler failed", description)
                return None
            if swallow_bad_request:
                logger.warning("%s bad request: %s", description, exc)
                return None
            raise
        except _RETRYABLE_TELEGRAM_ERRORS as exc:
            if attempt >= retries:
                logger.error(
                    "%s failed after %s attempts: %s",
                    description,
                    attempt,
                    exc,
                )
                if on_give_up is not None:
                    with suppress(Exception):
                        await on_give_up(exc)
                if raise_on_failure:
                    raise
                return None
            logger.warning(
                "%s failed (attempt %s/%s): %s",
                description,
                attempt,
                retries,
                exc,
            )
            await asyncio.sleep(base_delay * attempt)
        except Exception:
            logger.exception("%s unexpected error", description)
            if raise_on_failure:
                raise
            return None


async def _send_safe(bot: Bot, chat_id: int | str, text: str, *, message_thread_id: Optional[int] = None) -> None:
    async def _call() -> Any:
        return await bot.send_message(
            chat_id=chat_id, text=text, message_thread_id=message_thread_id
        )

    def _handle_bad_request(exc: TelegramBadRequest) -> None:
        details = str(exc).lower()
        if "kicked" in details:
            logger.warning("Bot removed from chat %s", chat_id)
        else:
            logger.warning("Failed to send message to %s: %s", chat_id, exc)

    async def _on_failure(exc: Exception) -> None:
        logger.warning(
            "bot.send_message to %s failed permanently: %s",
            chat_id,
            exc,
        )

    await _telegram_call(
        _call,
        description="bot.send_message",
        swallow_bad_request=True,
        bad_request_handler=_handle_bad_request,
        on_give_up=_on_failure,
    )


async def _answer_safe(message: Message, *args, **kwargs) -> Any:
    async def _on_failure(exc: Exception) -> None:
        logger.warning(
            "message.answer failed for chat %s message %s: %s",
            getattr(message.chat, "id", None),
            message.message_id,
            exc,
        )

    return await _telegram_call(
        lambda: message.answer(*args, **kwargs),
        description="message.answer",
        raise_on_failure=False,
        on_give_up=_on_failure,
    )


async def _edit_text_safe(message: Message, *args, **kwargs) -> Any:
    parse_mode = kwargs.get("parse_mode")
    reply_markup = kwargs.get("reply_markup")

    if args:
        text = args[0]
    else:
        text = kwargs.get("text")

    def _current_text() -> Optional[str]:
        if parse_mode == ParseMode.HTML:
            return message.html_text
        if parse_mode == ParseMode.MARKDOWN:
            return message.text
        return message.text

    def _dump_markup(kb: Optional[InlineKeyboardMarkup]) -> Optional[tuple]:
        if kb is None:
            return None
        try:
            return tuple(tuple(repr(btn) for btn in row) for row in kb.inline_keyboard)
        except Exception:
            return None

    if text is not None:
        current = _current_text()
        if current == text:
            current_markup = _dump_markup(message.reply_markup)
            new_markup = _dump_markup(reply_markup)
            if current_markup == new_markup:
                logger.debug(
                    "Skip editing message %s in chat %s: content unchanged",
                    message.message_id,
                    getattr(message.chat, "id", None),
                )
                return message

    async def _on_failure(exc: Exception) -> None:
        logger.warning(
            "message.edit_text failed for chat %s message %s: %s",
            getattr(message.chat, "id", None),
            message.message_id,
            exc,
        )

    try:
        return await _telegram_call(
            lambda: message.edit_text(*args, **kwargs),
            description="message.edit_text",
            swallow_bad_request=False,
            on_give_up=_on_failure,
        )
    except TelegramBadRequest as exc:
        details = str(exc).lower()
        if "message is not modified" in details:
            logger.debug(
                "Skip editing message %s: Telegram says not modified",
                message.message_id,
            )
            return message
        raise


async def _callback_answer_safe(query: CallbackQuery, *args, **kwargs) -> Any:
    async def _on_failure(exc: Exception) -> None:
        logger.warning(
            "callback.answer failed for chat %s query %s: %s",
            getattr(getattr(query.message, "chat", None), "id", None),
            query.id,
            exc,
        )

    return await _telegram_call(
        lambda: query.answer(*args, **kwargs),
        description="callback.answer",
        swallow_bad_request=True,
        on_give_up=_on_failure,
    )


def _ack_callback_background(query: CallbackQuery, *args, **kwargs) -> asyncio.Task[Any]:
    async def _run() -> None:
        try:
            await _callback_answer_safe(query, *args, **kwargs)
        except Exception:
            logger.debug("Background callback answer failed", exc_info=True)

    task = asyncio.create_task(_run())
    return task


def _apply_offset(dt: datetime, minutes: int) -> datetime:
    return dt - timedelta(minutes=minutes)


def _extract_chat_id(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _sync_job_schedule(job: Dict[str, Any]) -> Optional[datetime]:
    run_iso = job.get("run_at_utc")
    if not isinstance(run_iso, str):
        return None
    try:
        run_at = datetime.fromisoformat(run_iso)
    except Exception:
        return None
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)

    cfg_chat_id = _extract_chat_id(job.get("target_chat_id"))
    if cfg_chat_id is None:
        cfg_chat_id = _extract_chat_id(job.get("source_chat_id"))

    if cfg_chat_id is None:
        tz = timezone.utc
        default_offset = 30
    else:
        tz = storage.resolve_tz_for_chat(cfg_chat_id)
        default_offset = storage.get_offset_for_chat(cfg_chat_id)

    stored_offset = storage.normalize_offset(job.get("offset_minutes"), fallback=None)
    if stored_offset == 0 and job.get("offset_minutes") is None:
        offset_minutes = default_offset
    else:
        offset_minutes = storage.normalize_offset(stored_offset or job.get("offset_minutes"), fallback=default_offset)

    updates: Dict[str, Any] = {}
    if job.get("offset_minutes") != offset_minutes:
        job["offset_minutes"] = offset_minutes
        updates["offset_minutes"] = offset_minutes

    reminder_local = run_at.astimezone(tz)
    desired_local = reminder_local
    text = job.get("text")
    if isinstance(text, str) and text:
        parsed = parse_meeting_message(text, tz)
        if parsed:
            desired_local = _apply_offset(parsed["dt_local"], offset_minutes)

    if abs((desired_local - reminder_local).total_seconds()) >= 59:
        run_at = desired_local.astimezone(timezone.utc)
        job["run_at_utc"] = run_at.isoformat()
        updates["run_at_utc"] = job["run_at_utc"]

    job_id = job.get("job_id")
    if updates and isinstance(job_id, str):
        storage.upsert_job_record(job_id, updates)

    return run_at


def _resolve_target_title(target_chat_id: int | str) -> str:
    for known in storage.get_known_chats():
        if str(known.get("chat_id")) == str(target_chat_id):
            title = known.get("title")
            if title:
                return title
    return str(target_chat_id)


def _debounce(user_id: int, cooldown: float = 0.75) -> bool:
    now = time.monotonic()
    last = _debounce.cache.get(user_id, 0.0)
    if now - last < cooldown:
        return False
    _debounce.cache[user_id] = now
    return True


_debounce.cache: dict[int, float] = {}

async def _ensure_known_chat(message: Message) -> None:
    chat = message.chat
    if chat.type in {"group", "supergroup"}:
        title = chat.title or (chat.username and f"@{chat.username}") or str(chat.id)
        storage.register_chat(chat.id, title, topic_id=message.message_thread_id)


async def _ensure_reply_menu(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    data = await state.get_data()
    if data.get(STATE_REPLY_MENU_SHOWN):
        return
    allow_settings = message.chat.type == "private" or _is_admin(message.from_user)
    await _answer_safe(
        message,
        "👇 Быстрые действия",
        reply_markup=ui_kb.reply_menu_kb(
            _is_admin(message.from_user),
            allow_settings=allow_settings,
        ),
    )
    await state.update_data({STATE_REPLY_MENU_SHOWN: True})


async def _reset_interaction_state(
    state: FSMContext, *, preserve_pending: bool = False
) -> None:
    """Сбрасывает все флаги ожиданий и отложенные операции."""

    data = await state.get_data()
    updates: Dict[str, Any] = {}

    if data.get(STATE_AWAIT_TZ):
        updates[STATE_AWAIT_TZ] = False
    if data.get(STATE_AWAIT_ADMIN_ADD):
        updates[STATE_AWAIT_ADMIN_ADD] = False
    if data.get(STATE_AWAIT_ADMIN_DEL):
        updates[STATE_AWAIT_ADMIN_DEL] = False
    if not preserve_pending and data.get(STATE_PENDING):
        updates[STATE_PENDING] = {}

    if updates:
        await state.update_data(updates)


async def _pick_target_for_private(message: Message, state: FSMContext, text: str) -> bool:
    user = message.from_user
    if user is None:
        return False
    candidates: list[Dict[str, Any]] = []
    for candidate in storage.get_known_chats():
        chat_id = candidate.get("chat_id")
        member = await _telegram_call(
            lambda: message.bot.get_chat_member(chat_id, user.id),
            description="bot.get_chat_member",
            swallow_bad_request=True,
        )
        if member is None:
            continue
        if member.status not in {"left", "kicked"}:
            candidates.append(candidate)
    if not candidates:
        return False
    token = uuid.uuid4().hex
    data = await state.get_data()
    pending = dict(data.get(STATE_PENDING, {}))
    pending[token] = {"text": text}
    await state.update_data({STATE_PENDING: pending})
    candidates.append({"chat_id": message.chat.id, "title": "Личный чат"})
    await _answer_safe(
        message,
        "📨 Куда отправить напоминание?",
        reply_markup=ui_kb.choose_chat_kb(candidates, token, is_admin=_is_admin(message.from_user)),
    )
    return True


async def _get_valid_last_target(
    message: Message,
    user: Optional[User],
    state: FSMContext,
    data: Dict[str, Any],
) -> Optional[tuple[int | str, Optional[int]]]:
    entry = data.get(STATE_LAST_TARGET)
    if not isinstance(entry, dict):
        return None
    chat_id = entry.get("chat_id")
    topic_id = entry.get("topic_id")
    if chat_id is None:
        return None
    current_topic = message.message_thread_id or 0
    if chat_id == message.chat.id and int(topic_id or 0) == int(current_topic):
        return chat_id, topic_id

    for candidate in storage.get_known_chats():
        candidate_chat = candidate.get("chat_id")
        candidate_topic = candidate.get("topic_id") or 0
        if str(candidate_chat) != str(chat_id) or int(candidate_topic) != int(topic_id or 0):
            continue
        if not user or not isinstance(chat_id, int):
            return chat_id, topic_id
        member = await _telegram_call(
            lambda: message.bot.get_chat_member(chat_id, user.id),
            description="bot.get_chat_member",
            swallow_bad_request=True,
        )
        if member and member.status not in {"left", "kicked"}:
            return chat_id, topic_id
        break

    await state.update_data({STATE_LAST_TARGET: None})
    return None


async def schedule_reminder(
    *,
    message: Message,
    source_chat_id: int,
    target_chat_id: int | str,
    user: Optional[User],
    text: str,
    topic_id: Optional[int] = None,
    notify: bool = True,
) -> None:
    tz = storage.resolve_tz_for_chat(int(target_chat_id) if isinstance(target_chat_id, int) else source_chat_id)
    parsed = parse_meeting_message(text, tz)
    if not parsed:
        if notify:
            await _answer_safe(message,
                "🙈 Не понял формат. Жду: `ДД.ММ ТИП ЧЧ:ММ ПЕРЕГ НОМЕР`",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    if storage.find_job_by_text(parsed["reminder_text"]):
        if notify:
            await _answer_safe(message, "⚠️ Такая напоминалка уже есть.")
        return

    cfg_chat_id = _extract_chat_id(target_chat_id)
    if cfg_chat_id is None:
        cfg_chat_id = _extract_chat_id(source_chat_id)

    if cfg_chat_id is not None:
        offset_minutes = storage.get_offset_for_chat(cfg_chat_id)
    else:
        offset_minutes = storage.normalize_offset(None, fallback=30)
    reminder_local = _apply_offset(parsed["dt_local"], offset_minutes)
    reminder_utc = reminder_local.astimezone(timezone.utc)
    now_utc = _utc_now()

    job_id = f"rem-{uuid.uuid4().hex}"
    job_data = {
        "job_id": job_id,
        "target_chat_id": target_chat_id,
        "topic_id": topic_id,
        "text": parsed["reminder_text"],
        "source_chat_id": source_chat_id,
        "target_title": _resolve_target_title(target_chat_id),
        "author_id": getattr(user, "id", None),
        "author_username": getattr(user, "username", None),
        "created_at_utc": now_utc.isoformat(),
        "signature": f"{target_chat_id}:{parsed['canonical_full']}",
        "rrule": constants.RR_ONCE,
        "run_at_utc": reminder_utc.isoformat(),
        "offset_minutes": offset_minutes,
    }

    if reminder_utc <= now_utc:
        audit_log(
            "REM_SEND_NOW",
            chat_id=target_chat_id,
            topic_id=topic_id,
            title=parsed["reminder_text"],
            user_id=getattr(user, "id", None),
        )
        await _send_safe(message.bot, target_chat_id, job_data["text"], message_thread_id=topic_id)
        if notify:
            await _answer_safe(message, "✅ Напоминание уже должно было прийти — отправил сразу.")
        return

    _schedule_job(job_id, reminder_utc)
    storage.add_job_record(job_data)
    audit_log(
        "REM_SCHEDULED",
        reminder_id=job_id,
        chat_id=target_chat_id,
        topic_id=topic_id,
        user_id=getattr(user, "id", None),
        title=job_data["text"],
        when=reminder_utc,
        tz=getattr(tz, "zone", str(tz)),
        delay_sec=round((reminder_utc - now_utc).total_seconds(), 1),
    )
    if notify:
        await _answer_safe(message,
            f"📌 Запланировано на {reminder_local:%d.%m %H:%M}\n{parsed['canonical_full']}",
            reply_markup=ui_kb.job_kb(job_id) if _is_admin(user) else None,
            parse_mode=ParseMode.MARKDOWN,
        )

def _render_active(
    chunk: Iterable[Dict[str, Any]],
    total: int,
    page: int,
    pages_total: int,
    user: Optional[User],
    *,
    title: str,
    page_prefix: str,
    empty_message: str,
    view: str,
) -> tuple[str, InlineKeyboardMarkup]:
    admin = _is_admin(user)
    text = ui_txt.render_active_text(
        list(chunk),
        total,
        page,
        pages_total,
        admin,
        title=title,
        empty_message=empty_message,
    )
    kb = ui_kb.active_kb(
        list(chunk),
        page,
        pages_total,
        uid=user.id if user else 0,
        is_admin=admin,
        page_prefix=page_prefix,
        view=view,
    )
    return text, kb


async def _show_active(
    message: Message,
    user: Optional[User],
    *,
    page: int = 1,
    mine: bool = False,
) -> None:
    if not mine and not _is_admin(user):
        await _answer_safe(message, "⛔ Только администратор может просматривать активные напоминания.")
        return
    predicate: Optional[Callable[[Dict[str, Any]], bool]] = None
    title = "📝 Активные"
    page_prefix = constants.CB_ACTIVE_PAGE
    empty_message = "Пока нет активных напоминаний."
    view = "all"
    if mine:
        if not user:
            await _answer_safe(message, "⚠️ Доступно только пользователям.")
            return
        uid = user.id
        username = (user.username or "").lower()

        def predicate(job: Dict[str, Any]) -> bool:
            if job.get("author_id") == uid:
                return True
            if username and isinstance(job.get("author_username"), str):
                return job["author_username"].lower() == username
            return False

        title = "📂 Мои встречи"
        page_prefix = constants.CB_MY_PAGE
        empty_message = "У вас пока нет запланированных встреч."
        view = "my"
    chunk, total, pages_total = _paginate_jobs(
        page,
        constants.PAGE_SIZE or 10,
        predicate=predicate,
    )
    text, kb = _render_active(
        chunk,
        total,
        page,
        pages_total,
        user,
        title=title,
        page_prefix=page_prefix,
        empty_message=empty_message,
        view=view,
    )
    if message:
        try:
            await _edit_text_safe(message, text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await _answer_safe(message, text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def _show_create_hint(message: Message, user: Optional[User]) -> None:
    text = ui_txt.create_reminder_hint(message.chat.id)
    kb = ui_kb.main_menu_kb(
        _is_admin(user),
        allow_settings=_can_manage_settings(user, message.chat),
    )
    try:
        await _edit_text_safe(message, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except TelegramBadRequest:
        await _answer_safe(message, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def _show_settings(message: Message, user: Optional[User], state: FSMContext) -> None:
    if not _can_manage_settings(user, message.chat):
        await _answer_safe(message, "⛔ Только администратор может менять настройки.")
        return
    await state.update_data({STATE_AWAIT_TZ: False, STATE_AWAIT_ADMIN_ADD: False, STATE_AWAIT_ADMIN_DEL: False})
    kb = ui_kb.settings_menu_kb(_is_owner(user))
    text = "⚙️ Настройки"
    try:
        await _edit_text_safe(message, text, reply_markup=kb)
    except TelegramBadRequest:
        await _answer_safe(message, text, reply_markup=kb)


async def _show_chats(message: Message) -> None:
    known = storage.get_known_chats()
    kb = ui_kb.chats_menu_kb(known)
    text = "📋 Зарегистрированные чаты"
    try:
        await _edit_text_safe(message, text, reply_markup=kb)
    except TelegramBadRequest:
        await _answer_safe(message, text, reply_markup=kb)


async def _show_admins(message: Message) -> None:
    admins = constants.ADMIN_USERNAMES
    text = ui_txt.render_admins_text(admins)
    kb = ui_kb.admins_menu_kb(admins)
    try:
        await _edit_text_safe(message, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except TelegramBadRequest:
        await _answer_safe(message, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    try:
        return storage.get_job_record(job_id)
    except Exception:
        logger.exception("Failed to load job %s", job_id)
        return None


def _remove_job(job_id: str) -> None:
    storage.remove_job_record(job_id)
    with suppress(Exception):
        scheduler.remove_job(job_id)


async def _open_actions(
    message: Message,
    user: Optional[User],
    job_id: str,
    *,
    context: Optional[str] = None,
) -> None:
    job = _get_job(job_id)
    if not job:
        await _answer_safe(message, "Не найдено")
        return
    label = job.get("text", "напоминание")
    kb = ui_kb.actions_kb(job_id, is_admin=_is_admin(user), return_to=context)
    text = f"⚙️ Действия для «{label}»"
    try:
        await _edit_text_safe(message, text, reply_markup=kb)
    except TelegramBadRequest:
        await _answer_safe(message, text, reply_markup=kb)


def _update_job_time(job: Dict[str, Any], new_run: datetime) -> None:
    job["run_at_utc"] = new_run.astimezone(timezone.utc).isoformat()
    storage.upsert_job_record(job["job_id"], {"run_at_utc": job["run_at_utc"]})
    _schedule_job(job["job_id"], new_run)

async def send_reminder_job(job_id: str | None = None, **_: Any) -> None:
    if not job_id:
        return
    bot: Bot = send_reminder_job.bot  # type: ignore[attr-defined]
    job = _get_job(job_id)
    if not job:
        return
    audit_log(
        "REM_FIRED",
        reminder_id=job_id,
        chat_id=job.get("target_chat_id"),
        topic_id=job.get("topic_id"),
        title=job.get("text"),
        user_id=job.get("author_id"),
    )
    await _send_safe(bot, job.get("target_chat_id"), job.get("text", ""), message_thread_id=job.get("topic_id"))
    rrule = job.get("rrule", constants.RR_ONCE)
    run_iso = job.get("run_at_utc")
    try:
        run_at = datetime.fromisoformat(run_iso) if run_iso else _utc_now()
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
    except Exception:
        run_at = _utc_now()
    if rrule == constants.RR_DAILY:
        next_run = run_at + timedelta(days=1)
        _update_job_time(job, next_run)
        audit_log(
            "REM_RESCHEDULED",
            reminder_id=job_id,
            chat_id=job.get("target_chat_id"),
            topic_id=job.get("topic_id"),
            title=job.get("text"),
            user_id=job.get("author_id"),
            repeat_next_at=next_run,
            reason="repeat",
        )
    elif rrule == constants.RR_WEEKLY:
        next_run = run_at + timedelta(weeks=1)
        _update_job_time(job, next_run)
        audit_log(
            "REM_RESCHEDULED",
            reminder_id=job_id,
            chat_id=job.get("target_chat_id"),
            topic_id=job.get("topic_id"),
            title=job.get("text"),
            user_id=job.get("author_id"),
            repeat_next_at=next_run,
            reason="repeat",
        )
    else:
        _remove_job(job_id)


def restore_jobs() -> None:
    now = _utc_now()
    for job in storage.get_jobs_store():
        job_id = job.get("job_id")
        if not job_id:
            continue
        run_at = _sync_job_schedule(job)
        if run_at is None:
            continue
        delay = (run_at - now).total_seconds()
        if delay <= 0 and delay >= -constants.CATCHUP_WINDOW_SECONDS:
            asyncio.create_task(send_reminder_job(job_id))
        elif delay > 0:
            _schedule_job(job_id, run_at)

# === Commands ===


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = message.from_user
    text = ui_txt.menu_text_for(message.chat.id)
    await _answer_safe(
        message,
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ui_kb.main_menu_kb(
            _is_admin(user),
            allow_settings=_can_manage_settings(user, message.chat),
        ),
    )
    await _ensure_reply_menu(message, state)


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    user = message.from_user
    text = ui_txt.show_help_text()
    await _answer_safe(
        message,
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ui_kb.main_menu_kb(
            _is_admin(user),
            allow_settings=_can_manage_settings(user, message.chat),
        ),
    )
    await _ensure_reply_menu(message, state)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await cmd_start(message, state)


@router.message(Command("register"))
async def cmd_register(message: Message) -> None:
    await _ensure_known_chat(message)
    await _answer_safe(message, "Чат зарегистрирован ✅")


@router.message(Command("purge"))
async def cmd_purge(message: Message) -> None:
    if not _is_admin(message.from_user):
        await _answer_safe(message, "Только для админов.")
        return
    storage.set_jobs_store([])
    scheduler.remove_all_jobs()
    await _answer_safe(message, "База напоминаний очищена ✅")

# === Text handlers ===


@router.message(F.chat.type == "private", F.text)
async def handle_private_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    data = await state.get_data()
    if data.get(STATE_AWAIT_TZ):
        try:
            pytz.timezone(text)
        except Exception:
            await _answer_safe(message, "Некорректная TZ. Пример: `Europe/Moscow`", parse_mode=ParseMode.MARKDOWN)
            return
        storage.update_chat_cfg(message.chat.id, tz=text)
        await state.update_data({STATE_AWAIT_TZ: False})
        await _answer_safe(message, f"TZ обновлена: `{text}`", parse_mode=ParseMode.MARKDOWN)
        return

    if data.get(STATE_AWAIT_ADMIN_ADD):
        await state.update_data({STATE_AWAIT_ADMIN_ADD: False})
        if not _is_owner(message.from_user):
            await _answer_safe(message, "Только владелец может управлять админами.")
            return
        username = text.lstrip("@").lower()
        if not username:
            await _answer_safe(message, "Нужен логин вида @username")
            return
        added = storage.add_admin_username(username)
        await _answer_safe(message, "✅ Добавлен" if added else "⚠️ Уже в списке")
        return

    if data.get(STATE_AWAIT_ADMIN_DEL):
        await state.update_data({STATE_AWAIT_ADMIN_DEL: False})
        removed = storage.remove_admin_username(text.lstrip("@"))
        await _answer_safe(message, "✅ Удалён" if removed else "⚠️ Не найден")
        return

    await _ensure_reply_menu(message, state)

    force_pick = bool(data.get(STATE_FORCE_PICK))
    last_target = await _get_valid_last_target(message, message.from_user, state, data)

    action = REPLY_MENU_ALIASES.get(text.casefold())
    if action:
        await _reset_interaction_state(state)
        user = message.from_user
        if action == "menu":
            menu_text = ui_txt.menu_text_for(message.chat.id)
            await _answer_safe(message,
                menu_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=ui_kb.main_menu_kb(
                    _is_admin(user),
                    allow_settings=_can_manage_settings(user, message.chat),
                ),
            )
        elif action == "create":
            await state.update_data({STATE_FORCE_PICK: True})
            await _show_create_hint(message, user)
        elif action == "my":
            await _show_active(message, user, page=1, mine=True)
        elif action == "active":
            await _show_active(message, user, page=1)
        elif action == "settings":
            await _show_settings(message, user, state)
        elif action == "help":
            help_text = ui_txt.show_help_text()
            await _answer_safe(message,
                help_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=ui_kb.main_menu_kb(
                    _is_admin(user),
                    allow_settings=_can_manage_settings(user, message.chat),
                ),
            )
        await _ensure_reply_menu(message, state)
        return

    tz_preview = storage.resolve_tz_for_chat(message.chat.id)
    looks_like_reminder = parse_meeting_message(text, tz_preview) is not None

    if looks_like_reminder and last_target and not force_pick:
        target_chat_id, topic_id = last_target
        await schedule_reminder(
            message=message,
            source_chat_id=message.chat.id,
            target_chat_id=target_chat_id,
            user=message.from_user,
            text=text,
            topic_id=topic_id,
        )
        await state.update_data({STATE_FORCE_PICK: False, STATE_LAST_TARGET: {"chat_id": target_chat_id, "topic_id": topic_id}})
        return

    if looks_like_reminder and await _pick_target_for_private(message, state, text):
        return

    await schedule_reminder(
        message=message,
        source_chat_id=message.chat.id,
        target_chat_id=message.chat.id,
        user=message.from_user,
        text=text,
        topic_id=message.message_thread_id,
    )

    if looks_like_reminder:
        await state.update_data({
            STATE_LAST_TARGET: {"chat_id": message.chat.id, "topic_id": message.message_thread_id},
            STATE_FORCE_PICK: False,
        })


@router.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def handle_group_text(message: Message) -> None:
    if not message.text or message.text.startswith("/"):
        return
    await _ensure_known_chat(message)
    await schedule_reminder(
        message=message,
        source_chat_id=message.chat.id,
        target_chat_id=message.chat.id,
        user=message.from_user,
        text=message.text.strip(),
        topic_id=message.message_thread_id,
        notify=False,
    )

# === Callback handling ===


@router.callback_query()
async def on_callback(query: CallbackQuery, state: FSMContext) -> None:
    data = query.data or ""
    user = query.from_user
    message = query.message

    if user and not data.startswith(CB_NOOP) and not _debounce(user.id):
        with suppress(Exception):
            await _callback_answer_safe(query, "⏳ Уже выполняю…", cache_time=1)
        return

    if data == CB_NOOP or data.startswith(f"{CB_NOOP}:"):
        with suppress(Exception):
            await _callback_answer_safe(query, "⏳ Уже выполняю…", cache_time=1)
        return

    if message is None:
        with suppress(Exception):
            await _callback_answer_safe(query, "Сообщение недоступно", show_alert=True)
        return

    _ack_callback_background(query, "В работе", cache_time=1)

    await _reset_interaction_state(
        state,
        preserve_pending=data.startswith(f"{constants.CB_PICK_CHAT}:")
    )

    if data == constants.CB_MENU:
        text = ui_txt.menu_text_for(message.chat.id)
        kb = ui_kb.main_menu_kb(
            _is_admin(user),
            allow_settings=_can_manage_settings(user, message.chat),
        )
        try:
            await _edit_text_safe(message, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except TelegramBadRequest:
            await _answer_safe(message, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        await _ensure_reply_menu(message, state)
        await _callback_answer_safe(query)
        return

    if data == constants.CB_HELP:
        text = ui_txt.show_help_text()
        kb = ui_kb.main_menu_kb(
            _is_admin(user),
            allow_settings=_can_manage_settings(user, message.chat),
        )
        try:
            await _edit_text_safe(message, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except TelegramBadRequest:
            await _answer_safe(message, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        await _ensure_reply_menu(message, state)
        await _callback_answer_safe(query)
        return

    if data == constants.CB_SETTINGS:
        await _show_settings(message, user, state)
        await _ensure_reply_menu(message, state)
        await _callback_answer_safe(query)
        return

    if data == constants.CB_CREATE:
        await state.update_data({STATE_FORCE_PICK: True})
        await _show_create_hint(message, user)
        await _ensure_reply_menu(message, state)
        await _callback_answer_safe(query)
        return

    if data == constants.CB_MY or data.startswith(f"{constants.CB_MY_PAGE}:"):
        page = 1
        if ":" in data:
            try:
                page = int(data.split(":", 1)[1])
            except ValueError:
                page = 1
        await _show_active(message, user, page=page, mine=True)
        await _ensure_reply_menu(message, state)
        await _callback_answer_safe(query)
        return

    if data == constants.CB_ACTIVE or data.startswith(f"{constants.CB_ACTIVE_PAGE}:"):
        page = 1
        if ":" in data:
            try:
                page = int(data.split(":", 1)[1])
            except ValueError:
                page = 1
        await _show_active(message, user, page=page)
        await _ensure_reply_menu(message, state)
        await _callback_answer_safe(query)
        return

    if data == constants.CB_SET_TZ:
        if not _is_admin(user):
            await _answer_safe(message, "⛔ Только администратор может менять настройки.")
            await _callback_answer_safe(query)
            return
        kb = ui_kb.tz_menu_kb()
        try:
            await _edit_text_safe(message, "Выберите таймзону", reply_markup=kb)
        except TelegramBadRequest:
            await _answer_safe(message, "Выберите таймзону", reply_markup=kb)
        await _callback_answer_safe(query)
        return

    if data == constants.CB_SET_TZ_LOCAL:
        if not _is_admin(user):
            await _answer_safe(message, "⛔ Только администратор может менять настройки.")
            await _callback_answer_safe(query)
            return
        tz_name = get_localzone_name()
        storage.update_chat_cfg(message.chat.id, tz=tz_name)
        await _answer_safe(message, f"TZ обновлена: {tz_name}")
        await _callback_answer_safe(query)
        return

    if data == constants.CB_SET_TZ_MOSCOW:
        if not _is_admin(user):
            await _answer_safe(message, "⛔ Только администратор может менять настройки.")
            await _callback_answer_safe(query)
            return
        storage.update_chat_cfg(message.chat.id, tz="Europe/Moscow")
        await _answer_safe(message, "TZ обновлена: Europe/Moscow")
        await _callback_answer_safe(query)
        return

    if data == constants.CB_SET_TZ_CHICAGO:
        if not _is_admin(user):
            await _answer_safe(message, "⛔ Только администратор может менять настройки.")
            await _callback_answer_safe(query)
            return
        storage.update_chat_cfg(message.chat.id, tz="America/Chicago")
        await _answer_safe(message, "TZ обновлена: America/Chicago")
        await _callback_answer_safe(query)
        return

    if data == constants.CB_SET_TZ_ENTER:
        if not _is_admin(user):
            await _answer_safe(message, "⛔ Только администратор может менять настройки.")
            await _callback_answer_safe(query)
            return
        await state.update_data({STATE_AWAIT_TZ: True})
        await _answer_safe(message, "Введи название таймзоны, например `Europe/Moscow`", parse_mode=ParseMode.MARKDOWN)
        await _callback_answer_safe(query)
        return

    if data == constants.CB_SET_OFFSET:
        if not _is_admin(user):
            await _answer_safe(message, "⛔ Только администратор может менять настройки.")
            await _callback_answer_safe(query)
            return
        kb = ui_kb.offset_menu_kb()
        try:
            await _edit_text_safe(message, "⏳ Выберите оффсет", reply_markup=kb)
        except TelegramBadRequest:
            await _answer_safe(message, "⏳ Выберите оффсет", reply_markup=kb)
        await _callback_answer_safe(query)
        return

    if data in {constants.CB_OFF_DEC, constants.CB_OFF_INC} or data.startswith("off_p"):
        if not _is_admin(user):
            await _answer_safe(message, "⛔ Только администратор может менять настройки.")
            await _callback_answer_safe(query)
            return
        entry = storage.get_chat_cfg_entry(message.chat.id)
        current = int(entry.get("offset", 30))
        if data == constants.CB_OFF_DEC:
            current = max(0, current - 5)
        elif data == constants.CB_OFF_INC:
            current += 5
        else:
            try:
                current = int(data.split("_p")[-1])
            except Exception:
                current = 30
        storage.update_chat_cfg(message.chat.id, offset=current)
        await _answer_safe(message, f"⏳ Оффсет: {current} мин")
        await _callback_answer_safe(query)
        return

    if data == constants.CB_CHATS:
        if not _is_admin(user):
            await _answer_safe(message, "⛔ Только администратор может менять настройки.")
            await _callback_answer_safe(query)
            return
        await _show_chats(message)
        await _callback_answer_safe(query)
        return

    if data.startswith(f"{constants.CB_CHAT_DEL}:"):
        if not _is_admin(user):
            await _answer_safe(message, "⛔ Только администратор может менять настройки.")
            await _callback_answer_safe(query)
            return
        parts = data.split(":")
        chat_id = parts[1] if len(parts) > 1 else None
        topic_id = int(parts[2]) if len(parts) > 2 else 0
        if chat_id is not None:
            storage.unregister_chat(chat_id, topic_id if topic_id else None)
            await _show_chats(message)
        await _callback_answer_safe(query, "Удалено")
        return

    if data == constants.CB_ADMINS:
        if not _is_owner(user):
            await _answer_safe(message, "⛔ Только владелец может управлять администраторами.")
            await _callback_answer_safe(query)
            return
        await _show_admins(message)
        await _callback_answer_safe(query)
        return

    if data == constants.CB_ADMIN_ADD:
        if not _is_owner(user):
            await _answer_safe(message, "⛔ Только владелец может управлять администраторами.")
            await _callback_answer_safe(query)
            return
        await state.update_data({STATE_AWAIT_ADMIN_ADD: True})
        await _answer_safe(message, "Введи @username для добавления")
        await _callback_answer_safe(query)
        return

    if data.startswith(f"{constants.CB_ADMIN_DEL}:"):
        if not _is_owner(user):
            await _answer_safe(message, "⛔ Только владелец может управлять администраторами.")
            await _callback_answer_safe(query)
            return
        username = data.split(":", 1)[1]
        removed = storage.remove_admin_username(username)
        await _answer_safe(message, "✅ Удалён" if removed else "⚠️ Не найден")
        await _callback_answer_safe(query)
        return

    if data.startswith(f"{constants.CB_PICK_CHAT}:"):
        parts = data.split(":")
        if len(parts) < 4:
            await _callback_answer_safe(query, "Некорректные данные", show_alert=True)
            return
        chat_id_raw, topic_raw, token = parts[1], parts[2], parts[3]
        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            chat_id = chat_id_raw
        topic_id = int(topic_raw) if topic_raw and topic_raw != "0" else None
        data_state = await state.get_data()
        pending = dict(data_state.get(STATE_PENDING, {}))
        entry = pending.pop(token, None)
        await state.update_data({STATE_PENDING: pending})
        if not entry:
            await _callback_answer_safe(query, "Истекло", show_alert=True)
            return
        await schedule_reminder(
            message=message,
            source_chat_id=message.chat.id,
            target_chat_id=chat_id,
            user=user,
            text=entry.get("text", ""),
            topic_id=topic_id,
        )
        await state.update_data({
            STATE_LAST_TARGET: {"chat_id": chat_id, "topic_id": topic_id},
            STATE_FORCE_PICK: False,
        })
        await _callback_answer_safe(query, "Готово")
        return

    if data.startswith(f"{constants.CB_ACTIONS}:"):
        parts = data.split(":")
        job_id = parts[1] if len(parts) > 1 else None
        if len(parts) > 2 and parts[2] == "close":
            target = parts[3] if len(parts) > 3 else None
            if target == "my":
                await _show_active(message, user, page=1, mine=True)
            else:
                await _show_active(message, user, page=1)
            await _callback_answer_safe(query)
            return
        if job_id:
            context = parts[2] if len(parts) > 2 else None
            await _open_actions(message, user, job_id, context=context)
            await _callback_answer_safe(query)
            return

    if data.startswith(f"{constants.CB_SENDNOW}:"):
        job_id = data.split(":", 1)[1]
        await send_reminder_job(job_id=job_id)
        await _callback_answer_safe(query, "Отправлено")
        return

    if data.startswith(f"{constants.CB_CANCEL}:"):
        job_id = data.split(":", 1)[1]
        job = _get_job(job_id)
        _remove_job(job_id)
        if job:
            audit_log(
                "REM_CANCELED",
                reminder_id=job_id,
                chat_id=job.get("target_chat_id"),
                topic_id=job.get("topic_id"),
                user_id=getattr(user, "id", None),
                title=job.get("text"),
                reason="manual",
            )
        await _show_active(message, user, page=1)
        await _callback_answer_safe(query, "Удалено")
        return

    if data.startswith(f"{constants.CB_SHIFT}:"):
        parts = data.split(":")
        if len(parts) < 3:
            await _callback_answer_safe(query, "Некорректные данные", show_alert=True)
            return
        job_id = parts[1]
        try:
            minutes = int(parts[2])
        except ValueError:
            minutes = 5
        job = _get_job(job_id)
        if not job:
            await _callback_answer_safe(query, "Не найдено", show_alert=True)
            return
        run_iso = job.get("run_at_utc")
        try:
            run_at = datetime.fromisoformat(run_iso) if run_iso else _utc_now()
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
        except Exception:
            run_at = _utc_now()
        new_run = run_at + timedelta(minutes=minutes)
        _update_job_time(job, new_run)
        audit_log(
            "REM_RESCHEDULED",
            reminder_id=job_id,
            chat_id=job.get("target_chat_id"),
            topic_id=job.get("topic_id"),
            title=job.get("text"),
            user_id=getattr(user, "id", None),
            when=new_run,
            reason="manual_shift",
        )
        await _callback_answer_safe(query, f"Сдвинуто на +{minutes} мин")
        return

    if data.startswith(f"{constants.CB_RRULE}:"):
        await _callback_answer_safe(query, "Повторы пока недоступны", show_alert=True)
        return

    await _callback_answer_safe(query, "Неизвестная кнопка", show_alert=True)

# === Lifecycle ===


async def on_startup(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Приветствие"),
    ]
    with suppress(Exception):
        await bot.set_my_commands(commands)
    send_reminder_job.bot = bot  # type: ignore[attr-defined]
    if not scheduler.running:
        scheduler.start()
    restore_jobs()
    storage.add_admin_username("panykovc")
    logger.info("Startup complete")


async def on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
    logger.info("Shutdown complete")


async def main() -> None:
    cfg = storage.get_cfg()
    token = (cfg.get("token") if isinstance(cfg, dict) else None) or constants.BOT_TOKEN
    if not token:
        raise SystemExit("Token not configured")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(ErrorsMiddleware())
    dp.callback_query.middleware(ErrorsMiddleware())
    dp.include_router(router)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
