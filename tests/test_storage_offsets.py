from datetime import datetime, timedelta, timezone

from telegram_meeting_bot.core import storage


def test_get_offset_for_chat_defaults(monkeypatch):
    monkeypatch.setattr(storage, "get_cfg", lambda: {})
    assert storage.get_offset_for_chat(123) == storage.DEFAULT_REMINDER_OFFSET_MINUTES


def test_get_offset_for_chat_invalid(monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_cfg",
        lambda: {"42": {"offset": "not-a-number"}},
    )
    assert storage.get_offset_for_chat(42) == storage.DEFAULT_REMINDER_OFFSET_MINUTES


def test_get_offset_for_chat_non_positive(monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_cfg",
        lambda: {"7": {"offset": 0}},
    )
    assert storage.get_offset_for_chat(7) == storage.DEFAULT_REMINDER_OFFSET_MINUTES


def test_compute_job_times_uses_offset(monkeypatch):
    tz = timezone.utc
    meeting_dt = datetime(2024, 11, 10, 17, 0, tzinfo=tz)

    def fake_parse(text, tz_arg):
        assert tz_arg is tz
        return {"dt_local": meeting_dt}

    monkeypatch.setattr(storage, "parse_meeting_message", fake_parse)
    monkeypatch.setattr(storage, "resolve_tz_for_chat", lambda chat_id: tz)
    monkeypatch.setattr(storage, "get_offset_for_chat", lambda chat_id: 30)

    reminder_local, meeting_local = storage.compute_job_times({"text": "10.11 TEST 17:00 2В", "target_chat_id": 1})

    assert meeting_local == meeting_dt
    assert reminder_local == meeting_dt - timedelta(minutes=30)
