"""Persistente Agent-Profile & Workflow-Konfiguration für Meta-Coach / UI.

Liegt unter `meta_coach_rules_dir` (standardmäßig `/app/rules`):
- `agent_profiles.json` – Eigenschaften & Rollen der Sub-Agenten
- `workflow_config.json` – Laufzeitparameter (z. B. max. Korrekturschleifen)
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

PROFILES_FILENAME = "agent_profiles.json"
WORKFLOW_FILENAME = "workflow_config.json"

# Statische Topologie (entspricht agents/graph.py) – Meta-Coach darf Edges
# beschreiben/annotieren, die Graph-Struktur selbst bleibt Code.
DEFAULT_EDGES: list[dict[str, Any]] = [
    {"from": "START", "to": "supervisor", "when": "jeder Lauf beginnt hier"},
    {
        "from": "supervisor",
        "to": "concept_builder",
        "when": "kein requirements_contract vorhanden",
    },
    {
        "from": "supervisor",
        "to": "human_escalation",
        "when": "Konzept-Freigabe nötig, max. Iterationen oder Eskalationsflag",
    },
    {
        "from": "supervisor",
        "to": "inventory_manager",
        "when": "Konzept freigegeben, noch kein stock_and_tool_context",
    },
    {
        "from": "supervisor",
        "to": "builder_3d",
        "when": "Inventar-Kontext da, noch kein generated_code",
    },
    {
        "from": "supervisor",
        "to": "validator",
        "when": "Code vorhanden, noch kein sandbox_result",
    },
    {
        "from": "supervisor",
        "to": "END",
        "when": "alle parts in completed_parts",
    },
    {
        "from": "concept_builder",
        "to": "supervisor",
        "when": "Contract/Sketch erzeugt → zurück zum Router",
    },
    {
        "from": "inventory_manager",
        "to": "supervisor",
        "when": "CONTEXT_INJECTION fertig",
    },
    {"from": "builder_3d", "to": "supervisor", "when": "build123d-Code erzeugt"},
    {
        "from": "validator",
        "to": "supervisor",
        "when": "Sandbox-Ergebnis / ggf. refinement_request",
    },
    {
        "from": "human_escalation",
        "to": "supervisor",
        "when": "User-Entscheidung (approve/revise) via interrupt()",
    },
    {
        "from": "validator",
        "to": "builder_3d",
        "when": "Loop: refinement_request.target=builder_3d (indirekt über Supervisor)",
        "loop": True,
    },
    {
        "from": "builder_3d",
        "to": "concept_builder",
        "when": "Loop: Geometrie-Konflikt → Concept (indirekt über Supervisor)",
        "loop": True,
    },
]

DEFAULT_AGENTS: dict[str, dict[str, Any]] = {
    "supervisor": {
        "display_name": "Supervisor",
        "role": "Zentraler Orchestrator und Router der LangGraph-Topologie.",
        "responsibilities": [
            "Entscheidet den nächsten Sub-Agenten anhand des States",
            "Zählt Korrekturschleifen und erzwingt Eskalation bei Limit",
            "Sichert fertige Teile in completed_parts und rückt zum nächsten Teil vor",
        ],
        "inputs": ["gesamter AgentState"],
        "outputs": ["Routing-Entscheidung", "completed_parts", "iteration_count"],
        "properties": {
            "guidance": "",
            "notes": "Routing ist codegesteuert; guidance dient der Meta-Coach-Dokumentation.",
        },
    },
    "concept_builder": {
        "display_name": "Concept Builder",
        "role": "Zerlegt Nutzerwünsche in CNC-fertigbare Teile und erzeugt den Requirements-Contract.",
        "responsibilities": [
            "Mehrteil-Dekomposition (LLM oder Heuristik)",
            "2D-Sketch / Konzeptfoto für Human-Approval",
            "gap_analysis und Annahmen dokumentieren",
        ],
        "inputs": ["user_prompt", "refinement_request (Feedback)"],
        "outputs": ["requirements_contract", "concept_sketch_svg", "concept_image_url"],
        "properties": {
            "guidance": "",
            "prefer_conservative_dimensions": True,
            "notes": "Zusätzliche guidance wird an den LLM-System-Prompt angehängt.",
        },
    },
    "inventory_manager": {
        "display_name": "Inventory Manager",
        "role": "Passt Contract an verfügbares Material/Werkzeug an und injiziert Kontext.",
        "responsibilities": [
            "Stock- und Tool-Matching",
            "Lessons-Learned-Regeln laden",
            "CONTEXT_INJECTION für den 3D Builder",
        ],
        "inputs": ["requirements_contract", "Inventar-DB", "Asset-Beschreibungen (notes)", "rules/*.json"],
        "outputs": ["stock_and_tool_context", "ggf. angepasster Contract"],
        "properties": {
            "guidance": "",
            "strict_tool_match": False,
            "notes": "",
        },
    },
    "builder_3d": {
        "display_name": "3D Builder",
        "role": "Erzeugt parametrischen build123d-Python-Code für das aktuelle Teil.",
        "responsibilities": [
            "Code-Generierung aus Contract + Inventar-Kontext",
            "Berücksichtigung von Refinement-Feedback des Validators",
        ],
        "inputs": ["requirements_contract", "stock_and_tool_context", "error_history"],
        "outputs": ["generated_code"],
        "properties": {
            "guidance": "",
            "prefer_simple_solids": True,
            "notes": "Zusätzliche guidance wird an den LLM-System-Prompt angehängt.",
        },
    },
    "validator": {
        "display_name": "Validator",
        "role": "Führt Code in der Docker-Sandbox aus und bewertet Erfolg/Fehler.",
        "responsibilities": [
            "Sandbox-Ausführung (STEP/STL-Export)",
            "Fehlerklassifikation und refinement_request",
        ],
        "inputs": ["generated_code"],
        "outputs": ["sandbox_result", "refinement_request", "error_history"],
        "properties": {
            "guidance": "",
            "notes": "Timeout kommt aus workflow_config.sandbox_timeout_seconds.",
        },
    },
    "human_escalation": {
        "display_name": "Human Escalation",
        "role": "Pausiert den Graphen (interrupt) und holt Nutzerentscheidungen ein.",
        "responsibilities": [
            "Konzept-Freigabe (approve/revise)",
            "Eskalationen bei fehlgeschlagenen Loops",
        ],
        "inputs": ["escalation_reason", "concept_*", "requirements_contract"],
        "outputs": ["concept_approved", "refinement_request", "User-Decision"],
        "properties": {
            "guidance": "",
            "notes": "",
        },
    },
}

DEFAULT_WORKFLOW: dict[str, Any] = {
    "max_iterations": 6,
    "sandbox_timeout_seconds": 20,
    "description": (
        "Hub-and-Spoke: Alle Fach-Agenten melden an den Supervisor zurück. "
        "Korrekturschleifen laufen über refinement_request.target."
    ),
    "notes": "",
}


def _rules_dir() -> Path:
    path = Path(settings.meta_coach_rules_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _profiles_path() -> Path:
    return _rules_dir() / PROFILES_FILENAME


def _workflow_path() -> Path:
    return _rules_dir() / WORKFLOW_FILENAME


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_agent_profiles() -> dict[str, Any]:
    path = _profiles_path()
    if not path.is_file():
        doc = {
            "agents": deepcopy(DEFAULT_AGENTS),
            "edges": deepcopy(DEFAULT_EDGES),
            "updated_at": None,
        }
        save_agent_profiles(doc)
        return doc
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        agents = _deep_merge(DEFAULT_AGENTS, raw.get("agents") or {})
        edges = raw.get("edges") or deepcopy(DEFAULT_EDGES)
        return {"agents": agents, "edges": edges, "updated_at": raw.get("updated_at")}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("agent_profiles.json unlesbar: %s", exc)
        return {
            "agents": deepcopy(DEFAULT_AGENTS),
            "edges": deepcopy(DEFAULT_EDGES),
            "updated_at": None,
        }


def save_agent_profiles(doc: dict[str, Any]) -> Path:
    path = _profiles_path()
    doc = deepcopy(doc)
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_workflow_config() -> dict[str, Any]:
    path = _workflow_path()
    if not path.is_file():
        doc = deepcopy(DEFAULT_WORKFLOW)
        save_workflow_config(doc)
        return doc
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _deep_merge(DEFAULT_WORKFLOW, raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("workflow_config.json unlesbar: %s", exc)
        return deepcopy(DEFAULT_WORKFLOW)


def save_workflow_config(doc: dict[str, Any]) -> Path:
    path = _workflow_path()
    doc = deepcopy(doc)
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def get_agent_guidance(agent_id: str) -> str:
    profiles = load_agent_profiles()
    agent = (profiles.get("agents") or {}).get(agent_id) or {}
    props = agent.get("properties") or {}
    guidance = (props.get("guidance") or "").strip()
    return guidance


def get_max_iterations() -> int:
    cfg = load_workflow_config()
    try:
        value = int(cfg.get("max_iterations", settings.agent_max_iterations))
        return max(1, min(value, 50))
    except (TypeError, ValueError):
        return settings.agent_max_iterations


def get_sandbox_timeout() -> int:
    cfg = load_workflow_config()
    try:
        value = int(cfg.get("sandbox_timeout_seconds", settings.sandbox_timeout_seconds))
        return max(5, min(value, 300))
    except (TypeError, ValueError):
        return settings.sandbox_timeout_seconds


def update_agent_property(agent_id: str, property_key: str, value: Any) -> dict[str, Any]:
    doc = load_agent_profiles()
    agents = doc.setdefault("agents", {})
    if agent_id not in agents:
        raise KeyError(f"Unbekannter Agent: {agent_id}")
    props = agents[agent_id].setdefault("properties", {})
    props[property_key] = value
    save_agent_profiles(doc)
    return agents[agent_id]


def update_workflow_keys(updates: dict[str, Any]) -> dict[str, Any]:
    cfg = load_workflow_config()
    allowed = {"max_iterations", "sandbox_timeout_seconds", "description", "notes"}
    for key, value in updates.items():
        if key in allowed:
            cfg[key] = value
    save_workflow_config(cfg)
    return cfg


def workflow_overview() -> dict[str, Any]:
    profiles = load_agent_profiles()
    config = load_workflow_config()
    return {
        "agents": profiles.get("agents", {}),
        "edges": profiles.get("edges", []),
        "config": config,
        "profiles_updated_at": profiles.get("updated_at"),
    }
