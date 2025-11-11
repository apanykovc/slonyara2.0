from __future__ import annotations

import logging
import tempfile
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Tuple
from zipfile import ZIP_DEFLATED, ZipFile

from .constants import LOGS_APP_DIR, LOGS_AUDIT_DIR, LOGS_ERROR_DIR

LOG_TYPE_APP = "app"
LOG_TYPE_AUDIT = "audit"
LOG_TYPE_ERROR = "error"

_LOG_SOURCES: dict[str, Tuple[Path, str]] = {
    LOG_TYPE_APP: (LOGS_APP_DIR, "app"),
    LOG_TYPE_AUDIT: (LOGS_AUDIT_DIR, "audit"),
    LOG_TYPE_ERROR: (LOGS_ERROR_DIR, "error"),
}

_PREVIEW_LIMIT_DEFAULT = 12


class ErrorBurstHandler(logging.Handler):
    """Trigger callback when too many error records appear in a short time."""

    def __init__(
        self,
        *,
        threshold: int = 5,
        window_seconds: float = 60.0,
        cooldown_seconds: float = 300.0,
    ) -> None:
        super().__init__(level=logging.ERROR)
        self.threshold = max(1, threshold)
        self.window_seconds = max(1.0, window_seconds)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._recent: deque[float] = deque()
        self._callback: Callable[[logging.LogRecord, int], None] | None = None
        self._last_alert: float = 0.0

    def set_callback(self, callback: Callable[[logging.LogRecord, int], None] | None) -> None:
        self._callback = callback

    def reset(self) -> None:
        self._recent.clear()
        self._last_alert = 0.0

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - exercised indirectly
        if record.levelno < logging.ERROR:
            return
        now = time.monotonic()
        self._recent.append(now)
        while self._recent and now - self._recent[0] > self.window_seconds:
            self._recent.popleft()
        count = len(self._recent)
        if count < self.threshold:
            return
        if self.cooldown_seconds and now - self._last_alert < self.cooldown_seconds:
            return
        if not self._callback:
            return
        self._last_alert = now
        try:
            self._callback(record, count)
        except Exception:  # pragma: no cover - defensive
            logging.getLogger(__name__).exception("Error burst callback failed")


ERROR_BURST_MONITOR = ErrorBurstHandler()


def set_error_burst_callback(callback: Callable[[logging.LogRecord, int], None] | None) -> None:
    """Register callback that will be invoked on error burst."""

    ERROR_BURST_MONITOR.set_callback(callback)


def iter_log_files(log_type: str | None = None) -> Iterator[Tuple[str, Path]]:
    """Yield known log files grouped by type."""

    sources: Iterable[Tuple[str, Tuple[Path, str]]]
    if log_type:
        key = log_type.lower()
        if key not in _LOG_SOURCES:
            raise ValueError(f"Unknown log type: {log_type}")
        sources = ((key, _LOG_SOURCES[key]),)
    else:
        sources = _LOG_SOURCES.items()

    for kind, (directory, prefix) in sources:
        try:
            paths = sorted(directory.glob(f"{prefix}_*.log"))
        except FileNotFoundError:
            continue
        for path in paths:
            if path.is_file():
                yield kind, path


def get_recent_entries(log_type: str, limit: int = _PREVIEW_LIMIT_DEFAULT) -> List[str]:
    """Return last ``limit`` log lines for the specified log type."""

    limit = max(1, limit)
    kind = log_type.lower()
    if kind not in _LOG_SOURCES:
        raise ValueError(f"Unknown log type: {log_type}")
    directory, prefix = _LOG_SOURCES[kind]
    lines: deque[str] = deque(maxlen=limit)
    try:
        paths = sorted(directory.glob(f"{prefix}_*.log"))
    except FileNotFoundError:
        return []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    lines.append(raw.rstrip("\n"))
        except OSError:
            continue
    return list(lines)


def build_logs_archive() -> Path:
    """Create ZIP archive with all log files and return its path."""

    tmp_dir = Path(tempfile.gettempdir())
    archive = tmp_dir / f"bot-logs-{uuid.uuid4().hex[:8]}.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zf:
        added = False
        for kind, path in iter_log_files():
            try:
                zf.write(path, arcname=f"{kind}/{path.name}")
                added = True
            except OSError:
                continue
        if not added:
            info = "Логи отсутствуют."
            zf.writestr("README.txt", info)
    return archive


def clear_all_logs() -> int:
    """Remove rotated files and truncate current log files.

    Returns number of files affected.
    """

    affected = 0
    for kind, (directory, prefix) in _LOG_SOURCES.items():
        try:
            paths = sorted(directory.glob(f"{prefix}_*.log"))
        except FileNotFoundError:
            continue
        if not paths:
            continue
        old = paths[:-1]
        current = paths[-1]
        for path in old:
            try:
                path.unlink()
                affected += 1
            except OSError:
                continue
        try:
            with current.open("w", encoding="utf-8"):
                affected += 1
        except OSError:
            continue
    ERROR_BURST_MONITOR.reset()
    return affected


def describe_log_type(log_type: str) -> str:
    labels = {
        LOG_TYPE_APP: "App",
        LOG_TYPE_AUDIT: "Audit",
        LOG_TYPE_ERROR: "Error",
    }
    key = log_type.lower()
    if key not in labels:
        raise ValueError(f"Unknown log type: {log_type}")
    return labels[key]
