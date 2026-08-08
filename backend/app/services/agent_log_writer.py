"""Schreibt Sub-Agenten-Unterhaltungsverläufe als zeitgestempelte .txt-Dateien
für den Meta-Coach (nicht für die UI – Nutzer-Feedback)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def write_agent_transcript_txt(
    session_id: str,
    transcript: list[dict[str, Any]],
    *,
    user_prompt: str | None = None,
    status: str = "unknown",
) -> Path | None:
    """Legt eine lesbare .txt unter `agent_logs_dir` an und gibt den Pfad zurück."""
    try:
        logs_dir = Path(settings.agent_logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_session = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:48]
        path = logs_dir / f"{stamp}_{safe_session}.txt"

        lines: list[str] = [
            f"# Werkstatt-Brain Agent-Transcript",
            f"# session_id={session_id}",
            f"# status={status}",
            f"# written_at={datetime.now(timezone.utc).isoformat()}",
            f"# entries={len(transcript)}",
            "",
        ]
        if user_prompt:
            lines.extend(["## User-Prompt", user_prompt.strip(), ""])

        lines.append("## Sub-Agenten-Verlauf")
        for i, entry in enumerate(transcript, start=1):
            ts = entry.get("ts", "")
            agent = entry.get("agent", "?")
            event = entry.get("event", "")
            summary = entry.get("summary", "")
            to_agent = entry.get("to_agent")
            arrow = f" → {to_agent}" if to_agent else ""
            lines.append(f"[{i:03d}] {ts} | {agent}.{event}{arrow}")
            lines.append(f"      {summary}")
            detail = entry.get("detail") or {}
            if detail:
                # Kompakte Detailzeilen für Meta-Coach-Heuristiken
                for key, value in list(detail.items())[:20]:
                    lines.append(f"      - {key}: {value!r}"[:500])
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Agent-Transcript geschrieben: %s", path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent-Transcript-Datei fehlgeschlagen: %s", exc)
        return None
