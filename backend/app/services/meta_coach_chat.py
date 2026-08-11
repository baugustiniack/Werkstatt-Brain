"""Interaktiver Meta-Coach-Chat: analysiert Agent-Logs und passt Profile/Workflow an."""

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
from app.services import meta_coach as meta_coach_service

logger = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_PROMPT = """Du bist der Meta-Coach des Werkstatt-Brain CAD/CAM-Multi-Agenten-Systems.
Dein Auftrag: gemeinsam mit dem Nutzer den Sub-Agenten-Workflow analysieren und optimieren.

Du hast Zugriff auf:
1) Agent-Profile (Rollen, Eigenschaften, guidance-Texte)
2) Workflow-Config (max_iterations, sandbox_timeout_seconds, notes)
3) Lessons-Learned-Regeln unter rules/
4) Zeitgestempelte Agent-Transcript-.txt-Dateien (Logging)

Sub-Agenten: supervisor, flexible_specialist, vv_manager, concept_builder, inventory_manager,
fertigung_specialist, builder_3d, validator, montage_manager, human_escalation.
Topologie: Hub-and-Spoke – alle kehren zum Supervisor zurück; Loops laufen über refinement_request.
Pipeline: Flexible → V&V (concept) → Concept → V&V (design) → Inventory → Fertigung → V&V (manufacturing) → 3D → Validator → Montage (Anleitung + Werkzeuge).

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt (kein Markdown drumherum):
{
  "reply": "Klartext-Antwort an den Nutzer auf Deutsch (Markdown in diesem String erlaubt)",
  "actions": [
    {
      "type": "update_agent_property",
      "agent": "builder_3d",
      "property": "guidance",
      "value": "..."
    },
    {
      "type": "update_workflow",
      "updates": {"max_iterations": 8, "notes": "..."}
    },
    {
      "type": "add_rule",
      "category": "geometry_conflict",
      "signature": "kurze Fehler-Signatur",
      "advice": "empfohlene Gegenmaßnahme",
      "example_prompt": null
    }
  ],
  "referenced_logs": ["dateiname.txt"]
}

Regeln:
- Ändere nur, was der Nutzer will oder was klar aus Logs folgt. Bei Unsicherheit nachfragen und actions=[].
- guidance-Texte sollen kurz, konkret und für den jeweiligen Agenten-Prompt geeignet sein.
- max_iterations nur zwischen 1 und 50, sandbox_timeout_seconds zwischen 5 und 300.
- category für add_rule: syntax_error | geometry_conflict | tool_mismatch | timeout | unknown
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
        return {"reply": text.strip(), "actions": [], "referenced_logs": []}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"reply": text.strip(), "actions": [], "referenced_logs": []}
    if not isinstance(data, dict):
        return {"reply": text.strip(), "actions": [], "referenced_logs": []}
    return {
        "reply": str(data.get("reply") or "").strip() or text.strip(),
        "actions": data.get("actions") if isinstance(data.get("actions"), list) else [],
        "referenced_logs": data.get("referenced_logs") if isinstance(data.get("referenced_logs"), list) else [],
    }


def _apply_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        kind = action.get("type")
        try:
            if kind == "update_agent_property":
                agent = str(action.get("agent") or "")
                prop = str(action.get("property") or "")
                if not agent or not prop:
                    continue
                workflow_store.update_agent_property(agent, prop, action.get("value"))
                applied.append({"type": kind, "agent": agent, "property": prop, "status": "ok"})
            elif kind == "update_workflow":
                updates = action.get("updates") or {}
                if not isinstance(updates, dict):
                    continue
                workflow_store.update_workflow_keys(updates)
                applied.append({"type": kind, "updates": updates, "status": "ok"})
            elif kind == "add_rule":
                category = str(action.get("category") or "unknown")
                signature = str(action.get("signature") or "").strip()
                if not signature:
                    continue

                class _FakeLog:
                    session_id = "meta-coach-chat"
                    error_traceback = signature
                    error_message = signature
                    prompt = action.get("example_prompt")

                path = meta_coach_service.update_rule_for_category(category, _FakeLog())  # type: ignore[arg-type]
                # Optional advice anhängen
                advice = action.get("advice")
                if advice:
                    doc = meta_coach_service.load_rules(category)
                    for rule in doc.get("rules") or []:
                        if rule.get("signature") == signature:
                            rule["advice"] = advice
                            break
                    meta_coach_service.save_rules(category, doc)
                applied.append(
                    {"type": kind, "category": category, "signature": signature, "path": str(path), "status": "ok"}
                )
            else:
                applied.append({"type": kind, "status": "ignored", "reason": "unbekannter action-type"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Meta-Coach-Action fehlgeschlagen (%s): %s", kind, exc)
            applied.append({"type": kind, "status": "error", "error": str(exc)})
    return applied


def chat(
    messages: list[dict[str, str]],
    *,
    log_names: list[str] | None = None,
    apply_actions: bool = True,
) -> dict[str, Any]:
    """Führt einen Meta-Coach-Turn aus. `messages` = Chat-Historie inkl. neuester User-Nachricht."""
    if not messages:
        raise ValueError("messages darf nicht leer sein.")

    overview = workflow_store.workflow_overview()
    log_index = list_log_files(limit=30)
    log_context = _gather_log_context(log_names)

    user_bundle = (
        "## Aktueller Workflow / Profile\n"
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
                "oder Cursor-API-Key hinterlegen, damit der Meta-Coach Logs analysieren und "
                "Profile anpassen kann.\n\n"
                f"Aktuell {len(log_index)} Log-Datei(en) verfügbar. "
                "Workflow-Profile können trotzdem manuell über die API gelesen werden."
            ),
            "actions": [],
            "applied_changes": [],
            "referenced_logs": log_names or [],
            "llm_configured": False,
        }

    raw = call_llm_text(SYSTEM_PROMPT, user_bundle, max_tokens=4096)
    parsed = _parse_coach_response(raw)
    applied: list[dict[str, Any]] = []
    if apply_actions and parsed["actions"]:
        applied = _apply_actions(parsed["actions"])

    return {
        "reply": parsed["reply"],
        "actions": parsed["actions"],
        "applied_changes": applied,
        "referenced_logs": parsed["referenced_logs"],
        "llm_configured": True,
        "workflow": workflow_store.workflow_overview() if applied else None,
    }
