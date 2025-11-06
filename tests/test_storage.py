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
    sys.modules["pytz"] = pytz_stub

if "tzlocal" not in sys.modules:
    tzlocal_stub = types.ModuleType("tzlocal")

    def _fake_get_localzone_name() -> str:
        return "UTC"

    tzlocal_stub.get_localzone_name = _fake_get_localzone_name  # type: ignore[attr-defined]
    sys.modules["tzlocal"] = tzlocal_stub

import pytest

from core import storage


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
