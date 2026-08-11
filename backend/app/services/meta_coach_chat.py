"""Interaktiver Meta-Coach-Chat: analysiert Agent-Logs und gibt Empfehlungen.

Kein direkter Durchgriff auf den Agent-Workflow. Änderungen nimmt nur der User
im Reiter „Agent Workflow“ vor (Workflow-DB).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.llm_client import call_llm_text, is_llm_configured
from app.config import settings
from app.services import agent_workflow_store as workflow_store

logger = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_PROMPT = """Du bist der Meta-Coach des Werkstatt-Brain CAD/CAM-Multi-Agenten-Systems.
Dein Auftrag: Agent-Logs analysieren und dem Nutzer konkrete Empfehlungen geben,
wie er den Agent-Workflow optimieren kann.

WICHTIG: Du hast KEINEN direkten Zugriff auf den Workflow. Du darfst nichts speichern
oder ändern. Nur Empfehlungen – der User setzt sie manuell im Reiter „Agent Workflow“ um
(Konfiguration speichern / aktivieren).

Du hast Zugriff auf:
1) Die aktuell aktive Workflow-Konfiguration (Profile, enabled_agents, Config) – nur lesend
2) Zeitgestempelte Agent-Transcript-.txt-Dateien (Logging)

Fixe Agenten (immer aktiv): nur supervisor.
Optional: flexible_specialist, custom_agent_1, custom_agent_2 (Leer-Agenten nur via Guidance),
vv_manager, concept_builder, inventory_manager, fertigung_specialist, builder_3d, validator,
montage_manager, human_escalation.
Leer-Agenten sind in der Standard-Konfiguration deaktiviert.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt (kein Markdown drumherum):
{
  "reply": "Klartext-Antwort an den Nutzer auf Deutsch (Markdown in diesem String erlaubt)",
  "recommendations": [
    {
      "type": "agent_guidance" | "enable_agent" | "disable_agent" | "workflow_config" | "process" | "other",
      "agent": "builder_3d | null",
      "title": "kurze Überschrift",
      "detail": "was der User konkret im Agent-Workflow ändern sollte",
      "priority": "high" | "medium" | "low"
    }
  ],
  "referenced_logs": ["dateiname.txt"]
}

Regeln:
- Nur Empfehlungen aus Log-Analyse und Workflow-Kontext. Bei Unsicherheit nachfragen und recommendations=[].
- Keine Action-Typen zum direkten Schreiben (kein update_agent_property / update_workflow / add_rule).
- guidance-Vorschläge kurz und umsetzbar formulieren.
- max_iterations nur zwischen 1 und 50, sandbox_timeout_seconds zwischen 5 und 300 vorschlagen.
"""


def list_log_files(*, include_processed: bool = True, limit: int = 80) -> list[dict[str, Any]]:
    logs_dir = Path(settings.agent_logs_dir)
    files: list[Path] = []
    if logs_dir.is_dir():
        files.extend(logs_dir.glob("*.txt"))
        if include_processed:
            processed = logs_dir / "processed"
            if processed.is_dir():
                files.extend(processed.glob("*.txt"))
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    result: list[dict[str, Any]] = []
    for path in files:
        try:
            stat = path.stat()
            result.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "processed": path.parent.name == "processed",
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        except OSError:
            continue
    return result


def read_log_file(name: str, *, max_chars: int = 60000) -> dict[str, Any]:
    """Liest eine Log-Datei aus agent_logs oder agent_logs/processed (kein Path-Traversal)."""
    safe = Path(name).name
    if safe != name or not safe.endswith(".txt"):
        raise FileNotFoundError("Ungültiger Log-Dateiname.")
    candidates = [
        Path(settings.agent_logs_dir) / safe,
        Path(settings.agent_logs_dir) / "processed" / safe,
    ]
    for path in candidates:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            truncated = len(text) > max_chars
            return {
                "name": safe,
                "path": str(path),
                "processed": path.parent.name == "processed",
                "content": text[:max_chars],
                "truncated": truncated,
            }
    raise FileNotFoundError(f"Log nicht gefunden: {safe}")


def _gather_log_context(log_names: list[str] | None, *, max_files: int = 5, max_chars_each: int = 8000) -> str:
    names = log_names or [f["name"] for f in list_log_files(limit=max_files)]
    chunks: list[str] = []
    for name in names[:max_files]:
        try:
            data = read_log_file(name, max_chars=max_chars_each)
            chunks.append(f"### LOG {data['name']}\n{data['content']}")
        except FileNotFoundError:
            chunks.append(f"### LOG {name}\n(nicht gefunden)")
    return "\n\n".join(chunks) if chunks else "(keine Log-Dateien vorhanden)"


def _parse_coach_response(text: str) -> dict[str, Any]:
    match = _JSON_BLOCK.search(text.strip())
    if not match:
        return {"reply": text.strip(), "recommendations": [], "referenced_logs": [], "actions": []}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"reply": text.strip(), "recommendations": [], "referenced_logs": [], "actions": []}
    if not isinstance(data, dict):
        return {"reply": text.strip(), "recommendations": [], "referenced_logs": [], "actions": []}

    recommendations = data.get("recommendations")
    if not isinstance(recommendations, list):
        # Legacy: actions als Empfehlungen interpretieren (nicht anwenden)
        legacy = data.get("actions") if isinstance(data.get("actions"), list) else []
        recommendations = []
        for action in legacy:
            if not isinstance(action, dict):
                continue
            recommendations.append(
                {
                    "type": str(action.get("type") or "other"),
                    "agent": action.get("agent"),
                    "title": f"Empfehlung: {action.get('type')}",
                    "detail": json.dumps(action, ensure_ascii=False)[:800],
                    "priority": "medium",
                }
            )

    return {
        "reply": str(data.get("reply") or "").strip() or text.strip(),
        "recommendations": recommendations,
        "referenced_logs": data.get("referenced_logs") if isinstance(data.get("referenced_logs"), list) else [],
        # Kompatibilität: leere actions – kein Direct Write
        "actions": [],
    }


def chat(
    messages: list[dict[str, str]],
    *,
    log_names: list[str] | None = None,
    apply_actions: bool = False,  # noqa: ARG001 – absichtlich ignoriert (kein Direct Write)
) -> dict[str, Any]:
    """Führt einen Meta-Coach-Turn aus. Schreibt nie in den Workflow."""
    if not messages:
        raise ValueError("messages darf nicht leer sein.")

    overview = workflow_store.workflow_overview()
    log_index = list_log_files(limit=30)
    log_context = _gather_log_context(log_names)

    user_bundle = (
        "## Aktive Workflow-Konfiguration (nur lesend)\n"
        f"```json\n{json.dumps(overview, ensure_ascii=False, indent=2)[:12000]}\n```\n\n"
        "## Verfügbare Log-Dateien (Index)\n"
        f"```json\n{json.dumps(log_index, ensure_ascii=False, indent=2)[:4000]}\n```\n\n"
        "## Log-Inhalte (Auszug)\n"
        f"{log_context}\n\n"
        "## Chat-Verlauf\n"
    )
    for msg in messages[-12:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        user_bundle += f"\n[{role.upper()}]\n{content}\n"

    if not is_llm_configured():
        return {
            "reply": (
                "Kein LLM-Provider konfiguriert. Bitte unter Einstellungen einen Anthropic- "
                "oder Cursor-API-Key hinterlegen, damit der Meta-Coach Logs analysieren kann.\n\n"
                f"Aktuell {len(log_index)} Log-Datei(en) verfügbar. "
                "Workflow-Änderungen nimmst du im Reiter „Agent Workflow“ vor."
            ),
            "actions": [],
            "recommendations": [],
            "applied_changes": [],
            "referenced_logs": log_names or [],
            "llm_configured": False,
        }

    raw = call_llm_text(SYSTEM_PROMPT, user_bundle, max_tokens=4096)
    parsed = _parse_coach_response(raw)

    reply = parsed["reply"]
    recs = parsed.get("recommendations") or []
    if recs:
        bullets = "\n".join(
            f"- **{r.get('title') or r.get('type')}** ({r.get('priority') or 'medium'}): "
            f"{r.get('detail') or ''}"
            for r in recs
            if isinstance(r, dict)
        )
        if bullets and "Empfehlung" not in reply[:80]:
            reply = f"{reply}\n\n### Empfehlungen für den Agent-Workflow\n{bullets}"

    return {
        "reply": reply,
        "actions": [],
        "recommendations": recs,
        "applied_changes": [],
        "referenced_logs": parsed["referenced_logs"],
        "llm_configured": True,
        "workflow": None,
    }
