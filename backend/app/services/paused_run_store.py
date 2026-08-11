"""Persistente Pause-Snapshots für CAD-Workflows (Abbrechen / Standby / Restart).

In-Memory `PAUSED_RUNS` reicht nicht: bei WebSocket-Disconnect (Standby) und
Backend-Neustart müssen completed_parts + Konzept wiederherstellbar sein.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_INDEX_NAME = "_index.json"


def _paused_root() -> Path:
    path = Path(settings.conversations_dir) / "_paused_runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(session_id: str) -> Path:
    return _paused_root() / f"{session_id}.json"


def _index_path() -> Path:
    return _paused_root() / _INDEX_NAME


def _load_index() -> dict[str, str]:
    path = _index_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_index(index: dict[str, str]) -> None:
    _index_path().write_text(json.dumps(index, indent=2), encoding="utf-8")


def _jsonable_state(values: dict[str, Any]) -> dict[str, Any]:
    """State ohne messages / nicht JSON-fähige Werte speichern."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key == "messages":
            continue
        try:
            json.dumps(value, ensure_ascii=False)
            out[key] = value
        except (TypeError, ValueError):
            logger.debug("Pause-Snapshot: Feld %s nicht serialisierbar – übersprungen", key)
    return out


def save_paused_run(
    session_id: str,
    values: dict[str, Any],
    *,
    conversation_id: str | None = None,
) -> Path:
    payload = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "values": _jsonable_state(values),
    }
    path = _session_path(session_id)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if conversation_id:
        index = _load_index()
        index[conversation_id] = session_id
        _save_index(index)
    logger.info(
        "Pause-Snapshot gespeichert (%s, parts=%s)",
        session_id,
        len((values or {}).get("completed_parts") or []),
    )
    return path


def load_paused_run(session_id: str) -> dict[str, Any] | None:
    path = _session_path(session_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return {
            "values": raw.get("values") or {},
            "conversation_id": raw.get("conversation_id"),
            "saved_at": raw.get("saved_at"),
        }
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Pause-Snapshot unlesbar (%s): %s", session_id, exc)
        return None


def load_paused_run_for_conversation(conversation_id: str) -> dict[str, Any] | None:
    index = _load_index()
    session_id = index.get(conversation_id)
    if not session_id:
        # Fallback: neueste Datei mit matching conversation_id scannen
        newest: tuple[float, dict[str, Any]] | None = None
        for path in _paused_root().glob("*.json"):
            if path.name == _INDEX_NAME:
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if raw.get("conversation_id") != conversation_id:
                continue
            mtime = path.stat().st_mtime
            if newest is None or mtime > newest[0]:
                newest = (
                    mtime,
                    {
                        "values": raw.get("values") or {},
                        "conversation_id": conversation_id,
                        "saved_at": raw.get("saved_at"),
                        "session_id": raw.get("session_id") or path.stem,
                    },
                )
        return newest[1] if newest else None
    loaded = load_paused_run(session_id)
    if loaded is not None:
        loaded["session_id"] = session_id
    return loaded


def delete_paused_run(session_id: str) -> None:
    path = _session_path(session_id)
    if path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Pause-Snapshot löschen fehlgeschlagen (%s): %s", session_id, exc)
    index = _load_index()
    changed = False
    for conv_id, sid in list(index.items()):
        if sid == session_id:
            del index[conv_id]
            changed = True
    if changed:
        _save_index(index)
