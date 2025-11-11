import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

import pytz
from tzlocal import get_localzone_name

from .constants import (
    ADMINS_PATH,
    ADMIN_USERNAMES,
    CFG_PATH,
    DEFAULT_TZ_NAME,
    JOBS_DB_PATH,
    LEGACY_JOBS_PATH,
    TARGETS_PATH,
)

logger = logging.getLogger("reminder-bot")


def load_json(path: Path | str, default, *, backup_corrupt: bool = False):
    p = Path(path)
    if not p.exists():
        return default
    try:
        if p.stat().st_size == 0:
            # WHY: пустой файл списка чатов не должен ломать загрузку
            return default
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        if backup_corrupt:
            timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            backup = p.with_suffix(p.suffix + f".corrupt.{timestamp}.bak")
            try:
                p.rename(backup)
                logger.warning("Файл %s повреждён, сохранена копия %s", p, backup)
            except OSError as rename_err:
                logger.error(
                    "Не удалось переименовать повреждённый %s: %s",
                    p,
                    rename_err,
                )
        else:
            logger.warning("Не удалось прочитать %s: %s", p, exc)
        return default
    except Exception as e:
        logger.warning("Не удалось прочитать %s: %s", p, e)
        return default


def save_json(path: Path | str, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    # WHY: os.replace обеспечивает атомарную запись даже между томами
    os.replace(tmp, p)


# Работа с конфигом -------------------------------------------------------

def get_cfg() -> Dict[str, Any]:
    return load_json(CFG_PATH, {})


def set_cfg(cfg: Dict[str, Any]) -> None:
    save_json(CFG_PATH, cfg)


def get_chat_cfg_entry(chat_id: int) -> Dict[str, Any]:
    cfg = get_cfg()
    return cfg.get(str(chat_id), {})


def update_chat_cfg(chat_id: int, **kwargs) -> None:
    cfg = get_cfg()
    entry = cfg.get(str(chat_id), {})
    entry.update(kwargs)
    cfg[str(chat_id)] = entry
    set_cfg(cfg)


# Работа с заданиями ------------------------------------------------------

def migrate_legacy_json(
    json_path: Path = LEGACY_JOBS_PATH, db_path: Path = JOBS_DB_PATH
) -> int:
    """Импортировать старый JSON в SQLite.

    Возвращает количество перенесённых записей."""

    jpath = Path(json_path)
    if not jpath.exists():
        return 0
    try:
        data = load_json(jpath, [])
    except Exception as e:
        logger.warning("Не удалось прочитать %s: %s", jpath, e)
        return 0

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS reminders (job_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archived_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            archived_at TEXT NOT NULL,
            data TEXT NOT NULL
        )
        """
    )
    count = 0
    with conn:
        for rec in data:
            jid = rec.get("job_id")
            if not jid:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO reminders (job_id, data) VALUES (?, ?)",
                (jid, json.dumps(rec, ensure_ascii=False)),
            )
            count += 1
    try:
        jpath.unlink()
    except Exception:
        pass
    return count


def _connect() -> sqlite3.Connection:
    """Вернуть соединение с БД напоминаний, создать при необходимости."""
    JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(JOBS_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS reminders (job_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archived_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            archived_at TEXT NOT NULL,
            data TEXT NOT NULL
        )
        """
    )
    # миграция со старого JSON, если таблица пустая
    try:
        cur = conn.execute("SELECT COUNT(*) AS c FROM reminders")
        if cur.fetchone()["c"] == 0:
            migrate_legacy_json()
    except Exception as e:
        logger.warning("Миграция напоминаний не удалась: %s", e)
    return conn


def get_jobs_store() -> list:
    with _connect() as conn:
        rows = conn.execute("SELECT data FROM reminders").fetchall()
    return [json.loads(r["data"]) for r in rows]


def set_jobs_store(items: list) -> None:
    with _connect() as conn, conn:
        conn.execute("DELETE FROM reminders")
        for rec in items:
            jid = rec.get("job_id")
            if jid:
                conn.execute(
                    "INSERT OR REPLACE INTO reminders (job_id, data) VALUES (?, ?)",
                    (jid, json.dumps(rec, ensure_ascii=False)),
                )


def add_job_record(rec: Dict[str, Any]) -> None:
    jid = rec.get("job_id")
    if not jid:
        return
    with _connect() as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO reminders (job_id, data) VALUES (?, ?)",
            (jid, json.dumps(rec, ensure_ascii=False)),
        )


def remove_job_record(job_id: str) -> None:
    with _connect() as conn, conn:
        conn.execute("DELETE FROM reminders WHERE job_id = ?", (job_id,))


def get_job_record(job_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT data FROM reminders WHERE job_id = ?", (job_id,)
        ).fetchone()
    return json.loads(row["data"]) if row else None


def find_job_by_text(text: str) -> Optional[Dict[str, Any]]:
    """Найти напоминание по его тексту."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT data FROM reminders WHERE json_extract(data, '$.text') = ?",
            (text,),
        ).fetchone()
    return json.loads(row["data"]) if row else None


def archive_job(
    job_id: str,
    rec: Optional[Dict[str, Any]] = None,
    *,
    reason: str,
    removed_by: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """Перенести напоминание в архив и удалить из активных."""

    if not job_id:
        return False

    record = dict(rec or {}) if rec else None
    if record is None:
        record = get_job_record(job_id)
    if not record:
        return False

    payload = dict(record)
    payload.setdefault("job_id", job_id)
    payload["archive_reason"] = reason
    payload["archived_at_utc"] = datetime.utcnow().replace(microsecond=0).isoformat()
    if removed_by:
        payload["removed_by"] = removed_by
    if extra:
        payload.update(extra)

    with _connect() as conn, conn:
        conn.execute(
            "INSERT INTO archived_reminders (job_id, archived_at, data) VALUES (?, ?, ?)",
            (
                payload.get("job_id"),
                payload["archived_at_utc"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.execute("DELETE FROM reminders WHERE job_id = ?", (job_id,))
    return True


def get_jobs_for_chat(
    chat_id: Union[int, str],
    topic_id: Optional[int] = None,
) -> list[Dict[str, Any]]:
    """Получить список напоминаний, связанных с указанным чатом/темой."""

    chat_key = str(chat_id)
    topic_key = None if topic_id is None else int(topic_id)
    result: list[Dict[str, Any]] = []
    for rec in get_jobs_store():
        target_chat = rec.get("target_chat_id")
        if str(target_chat) != chat_key:
            continue
        if topic_key is not None:
            rec_topic = rec.get("topic_id") or 0
            try:
                rec_topic_val = int(rec_topic)
            except (TypeError, ValueError):
                rec_topic_val = 0
            if rec_topic_val != topic_key:
                continue
        result.append(rec)
    return result


def archive_jobs_for_chat(
    chat_id: Union[int, str],
    topic_id: Optional[int] = None,
    *,
    reason: str,
    removed_by: Optional[Dict[str, Any]] = None,
) -> int:
    """Архивировать все напоминания, связанные с указанным чатом."""

    jobs = get_jobs_for_chat(chat_id, topic_id)
    count = 0
    for rec in jobs:
        job_id = rec.get("job_id")
        if not job_id:
            continue
        if archive_job(job_id, rec=rec, reason=reason, removed_by=removed_by):
            count += 1
    return count


def get_archive_page(
    page: int,
    page_size: int,
) -> tuple[list[Dict[str, Any]], int, int, int]:
    """Вернуть страницу архива и метаданные (items, total, page, pages_total)."""

    if page_size <= 0:
        raise ValueError("page_size must be positive")

    with _connect() as conn:
        total_row = conn.execute("SELECT COUNT(*) AS c FROM archived_reminders").fetchone()
        total = int(total_row["c"] if total_row else 0)
        if total == 0:
            return [], 0, 1, 1
        pages_total = max(1, (total + page_size - 1) // page_size)
        page = min(max(page, 1), pages_total)
        offset = (page - 1) * page_size
        rows = conn.execute(
            """
            SELECT data FROM archived_reminders
            ORDER BY datetime(archived_at) DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
    items = [json.loads(row["data"]) for row in rows]
    return items, total, page, pages_total


def clear_archive() -> int:
    """Удалить все записи архива. Возвращает количество удалённых элементов."""

    with _connect() as conn, conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM archived_reminders").fetchone()
        total = int(row["c"] if row else 0)
        conn.execute("DELETE FROM archived_reminders")
    return total


def upsert_job_record(job_id: str, updates: Dict[str, Any]) -> None:
    rec = get_job_record(job_id) or {"job_id": job_id}
    rec.update(updates)
    add_job_record(rec)


# Настройки чата ---------------------------------------------------------

def get_org_tz_name() -> str:
    """Вернуть таймзону организации с учётом окружения и дефолта."""

    env_tz = os.environ.get("ORG_TZ")
    if env_tz:
        return env_tz
    if DEFAULT_TZ_NAME:
        return DEFAULT_TZ_NAME
    try:
        return get_localzone_name()
    except Exception as exc:
        logger.warning("Не удалось определить локальную TZ (%s), используем UTC.", exc)
        return "UTC"


def resolve_tz_for_chat(chat_id: int) -> pytz.BaseTzInfo:
    entry = get_chat_cfg_entry(chat_id)
    tz_name = entry.get("tz")
    if tz_name:
        try:
            return pytz.timezone(tz_name)
        except Exception as e:
            logger.warning(
                "Некорректная TZ '%s' для чата %s (%s). Используем дефолт.",
                tz_name,
                chat_id,
                e,
            )

    fallback_tz = get_org_tz_name()
    try:
        return pytz.timezone(fallback_tz)
    except Exception as e:
        logger.warning(
            "Некорректная дефолтная TZ '%s' (%s). Падаем на UTC.",
            fallback_tz,
            e,
        )
        return pytz.utc


def normalize_offset(value: Any, fallback: int | None = 0) -> int:
    """Convert ``value`` to a non-negative integer offset in minutes."""

    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = fallback
    if minutes is None:
        minutes = fallback
    if minutes is None:
        return 0
    return max(0, minutes)


def get_offset_for_chat(chat_id: int) -> int:
    entry = get_chat_cfg_entry(chat_id)
    if "offset" not in entry:
        return 30
    return normalize_offset(entry.get("offset"), fallback=30)


# Работа со списком известных чатов ----------------------------------------

def get_known_chats() -> list:
    # WHY: защищаем список чатов от повреждённых файлов
    return load_json(TARGETS_PATH, [], backup_corrupt=True)


def register_chat(
    chat_id: Union[int, str],
    title: str,
    topic_id: int | None = None,
    topic_title: str | None = None,
) -> bool:
    """Добавить чат (и опционально тему) в список известных чатов.

    Возвращает *True*, если чат реально добавлен, и *False*, если он уже был
    в списке (дубликаты не записываются)."""
    chats = get_known_chats()
    cid = str(chat_id)
    tid = topic_id or 0
    for idx, c in enumerate(chats):
        if str(c.get("chat_id")) == cid and int(c.get("topic_id", 0)) == tid:
            updated = False
            new_entry = dict(c)
            if title and title != c.get("title"):
                new_entry["title"] = title
                updated = True
            if topic_id is not None and topic_id != c.get("topic_id"):
                new_entry["topic_id"] = topic_id
                updated = True
            if topic_title and topic_title != c.get("topic_title"):
                new_entry["topic_title"] = topic_title
                updated = True
            if updated:
                chats[idx] = new_entry
                save_json(TARGETS_PATH, chats)
            return False

    entry = {"chat_id": chat_id, "title": title}
    if topic_id is not None:
        entry["topic_id"] = topic_id
        if topic_title:
            entry["topic_title"] = topic_title
    save = chats + [entry]
    save_json(TARGETS_PATH, save)
    return True


def unregister_chat(chat_id: Union[int, str], topic_id: int | None = None) -> None:
    """Удалить чат/тему из списка зарегистрированных чатов."""
    cid = str(chat_id)
    tid = topic_id or 0
    chats = [
        c
        for c in get_known_chats()
        if not (str(c.get("chat_id")) == cid and int(c.get("topic_id", 0)) == tid)
    ]
    save_json(TARGETS_PATH, chats)


# ---------------------------------------------------------------------------
# Управление списком администраторов
# ---------------------------------------------------------------------------


def add_admin_username(username: str) -> bool:
    """Добавить логин в список админов. Возвращает True при успехе."""
    uname = username.lstrip("@").lower()
    if not uname:
        return False
    current = load_json(ADMINS_PATH, [])
    if uname in current:
        return False
    current.append(uname)
    save_json(ADMINS_PATH, current)
    ADMIN_USERNAMES.add(uname)
    return True


def remove_admin_username(username: str) -> bool:
    """Удалить логин из списка админов."""
    uname = username.lstrip("@").lower()
    current = load_json(ADMINS_PATH, [])
    if uname not in current:
        return False
    current = [u for u in current if u != uname]
    save_json(ADMINS_PATH, current)
    ADMIN_USERNAMES.discard(uname)
    return True
