from __future__ import annotations
from typing import Optional, Dict, Any
from datetime import datetime
import logging

from .constants import MEETING_REGEX

logger = logging.getLogger("reminder-bot.aiogram")

def parse_meeting_message(text: str, tz) -> Optional[Dict[str, Any]]:
    """Парсит строку вида '08.08 МТС 20:40 2в 88634'.
    Возвращает dict с ключами: date (aware datetime), typ, room, req
    """
    m = MEETING_REGEX.match(text or "")
    if not m:
        return None
    try:
        d = int(m['d'])
        mth = int(m['mth'])
        typ = m['typ'].strip()
        hh = int(m['hh'])
        mm = int(m['mm'])
        room = m['room'].strip()
        req = m['req'].strip()

        now = datetime.now(tz)
        year = now.year

        candidate = datetime(year, mth, d, hh, mm)
        # pytz vs zoneinfo
        if hasattr(tz, "localize"):
            candidate = tz.localize(candidate)        # pytz
        else:
            candidate = candidate.replace(tzinfo=tz)  # zoneinfo

        if candidate < now:
            # если дата уже прошла — пробуем следующий год
            candidate = candidate.replace(year=year + 1)

        return {
            "date": candidate,
            "typ": typ,
            "room": room,
            "req": req,
        }
    except Exception as e:
        logger.exception("Ошибка парсинга строки встречи: %s", e)
        return None
