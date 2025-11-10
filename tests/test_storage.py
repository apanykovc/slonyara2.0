from __future__ import annotations
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "pytz" not in sys.modules:
    pytz_stub = types.ModuleType("pytz")
    pytz_stub.BaseTzInfo = object  # type: ignore[attr-defined]
    pytz_stub.timezone = lambda name: name  # type: ignore[assignment]
    pytz_stub.utc = "UTC"
    sys.modules["pytz"] = pytz_stub

if "tzlocal" not in sys.modules:
    tzlocal_stub = types.ModuleType("tzlocal")

    def _fake_get_localzone_name() -> str:
        return "UTC"

    tzlocal_stub.get_localzone_name = _fake_get_localzone_name  # type: ignore[attr-defined]
    sys.modules["tzlocal"] = tzlocal_stub

import pytest

from telegram_meeting_bot.core import storage


@pytest.fixture(autouse=True)
def isolate_storage_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    chats_path = tmp_path / "chats.json"
    monkeypatch.setattr(storage, "TARGETS_PATH", chats_path)
    yield


def _load_known_chats(path: Path) -> list:
    if not path.exists():
        return []
    return storage.load_json(path, [])


def test_register_chat_updates_title(tmp_path: Path):
    path = storage.TARGETS_PATH
    assert _load_known_chats(path) == []

    assert storage.register_chat(123, "Old title") is True
    chats = _load_known_chats(path)
    assert chats[0]["title"] == "Old title"

    assert storage.register_chat(123, "New title") is False
    chats = _load_known_chats(path)
    assert chats[0]["title"] == "New title"


def test_register_chat_updates_topic_title(tmp_path: Path):
    path = storage.TARGETS_PATH

    storage.register_chat(123, "Chat", topic_id=50, topic_title="Old topic")
    storage.register_chat(123, "Chat", topic_id=50, topic_title="New topic")

    chats = _load_known_chats(path)
    assert chats[0]["topic_title"] == "New topic"


def test_get_offset_defaults_to_30(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_id = 42
    storage.update_chat_cfg(chat_id, offset="not-a-number")
    assert storage.get_offset_for_chat(chat_id) == 30


def test_normalize_offset_handles_invalid() -> None:
    assert storage.normalize_offset(15) == 15
    assert storage.normalize_offset(-5) == 0
    assert storage.normalize_offset("bad", fallback=30) == 30


def test_resolve_tz_uses_default_moscow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORG_TZ", raising=False)
    monkeypatch.setattr(storage, "DEFAULT_TZ_NAME", "Europe/Moscow", raising=False)
    monkeypatch.setattr(storage, "get_chat_cfg_entry", lambda _cid: {})
    assert storage.resolve_tz_for_chat(100) == "Europe/Moscow"


def test_resolve_tz_invalid_chat_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_timezone(name: str) -> str:
        if name == "Bad/Zone":
            raise ValueError("invalid tz")
        return name

    monkeypatch.setattr(storage.pytz, "timezone", fake_timezone)
    monkeypatch.delenv("ORG_TZ", raising=False)
    monkeypatch.setattr(storage, "DEFAULT_TZ_NAME", "Europe/Moscow", raising=False)
    monkeypatch.setattr(storage, "get_chat_cfg_entry", lambda _cid: {"tz": "Bad/Zone"})

    assert storage.resolve_tz_for_chat(200) == "Europe/Moscow"


def test_resolve_tz_invalid_default_uses_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_timezone(name: str) -> str:
        if name in {"Bad/Zone", "Wrong/Zone"}:
            raise ValueError("invalid tz")
        return name

    monkeypatch.setattr(storage.pytz, "timezone", fake_timezone)
    monkeypatch.setenv("ORG_TZ", "Wrong/Zone")
    monkeypatch.setattr(storage, "DEFAULT_TZ_NAME", "", raising=False)
    monkeypatch.setattr(storage, "get_chat_cfg_entry", lambda _cid: {"tz": "Bad/Zone"})

    assert storage.resolve_tz_for_chat(300) == storage.pytz.utc
