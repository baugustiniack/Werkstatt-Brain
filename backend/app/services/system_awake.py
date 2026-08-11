"""Verhindert System-Ruhezustand während langer CAD-/Agent-Workflows.

Auf Windows über `SetThreadExecutionState` (ES_SYSTEM_REQUIRED). Refcount +
Hintergrund-Ping, damit parallele Sessions und lange LLM-/Sandbox-Läufe den
Idle-Timer zuverlässig zurücksetzen. Andere Plattformen: No-Op.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_AWAYMODE_REQUIRED = 0x00000040

_lock = threading.Lock()
_active = 0
_ping_stop = threading.Event()
_ping_thread: threading.Thread | None = None
_ping_interval_s = 30.0


def _set_execution_state(flags: int) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        if not result:
            logger.warning("SetThreadExecutionState(0x%X) fehlgeschlagen", flags)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ruhemodus-Sperre nicht gesetzt: %s", exc)


def _apply_awake() -> None:
    # SYSTEM_REQUIRED: Idle-Timer zurücksetzen (kein Ruhezustand)
    # AWAYMODE: erlaubt Media-/Away-Mode statt echtem Sleep (falls konfiguriert)
    _set_execution_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_AWAYMODE_REQUIRED)


def _clear_awake() -> None:
    _set_execution_state(_ES_CONTINUOUS)


def _ping_loop() -> None:
    while not _ping_stop.wait(_ping_interval_s):
        with _lock:
            if _active <= 0:
                break
        _apply_awake()


def _ensure_ping_thread() -> None:
    global _ping_thread
    if _ping_thread is not None and _ping_thread.is_alive():
        return
    _ping_stop.clear()
    _ping_thread = threading.Thread(target=_ping_loop, name="system-awake-ping", daemon=True)
    _ping_thread.start()


def acquire(reason: str = "workflow") -> None:
    """Eine aktive Workflow-Session hält den PC wach (refcount)."""
    global _active
    with _lock:
        _active += 1
        count = _active
        if count == 1:
            _apply_awake()
            _ensure_ping_thread()
    logger.debug("System-Awake acquire (%s) → active=%s", reason, count)


def release(reason: str = "workflow") -> None:
    global _active
    with _lock:
        if _active <= 0:
            _active = 0
            return
        _active -= 1
        count = _active
        if count == 0:
            _ping_stop.set()
            _clear_awake()
    logger.debug("System-Awake release (%s) → active=%s", reason, count)


@contextmanager
def keep_system_awake(reason: str = "workflow") -> Iterator[None]:
    acquire(reason)
    try:
        yield
    finally:
        release(reason)


def is_active() -> bool:
    with _lock:
        return _active > 0
