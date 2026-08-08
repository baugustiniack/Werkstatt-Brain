"""Hilfen für das Sub-Agenten-Unterhaltungsprotokoll (Analyse des Zusammenspiels).

Jeder Node hängt über den LangGraph-Reducer `append_transcript` Einträge an
`AgentState.agent_transcript` an. Bei Session-Ende landen sie als zeitgestempelte
`.txt` unter `agent_logs_dir` (Meta-Coach) und in `execution_logs.log_payload` –
nicht in der Chat-UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def append_transcript(left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """LangGraph-Reducer: konkateniert Transcript-Chunks aus Node-Updates."""
    return list(left or []) + list(right or [])


def make_entry(
    agent: str,
    event: str,
    summary: str,
    *,
    detail: dict[str, Any] | None = None,
    to_agent: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "event": event,
        "summary": summary,
        "detail": detail or {},
    }
    if to_agent:
        entry["to_agent"] = to_agent
    return entry


def truncate(text: str | None, limit: int = 1200) -> str | None:
    if text is None:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
