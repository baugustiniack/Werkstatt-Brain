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
        "to": "flexible_specialist",
        "when": "noch nicht flexible_consulted und Agent enabled",
    },
    {
        "from": "supervisor",
        "to": "interior_architect",
        "when": "noch nicht interior_consulted und Agent enabled",
    },
    {
        "from": "supervisor",
        "to": "custom_agent_1",
        "when": "Leer-Agent 1 enabled und noch nicht in empty_agents_consulted",
    },
    {
        "from": "supervisor",
        "to": "custom_agent_2",
        "when": "Leer-Agent 2 enabled und noch nicht in empty_agents_consulted",
    },
    {
        "from": "supervisor",
        "to": "vv_manager",
        "when": "V&V-Phase concept/design/manufacturing noch nicht in vv_consulted_phases",
    },
    {
        "from": "supervisor",
        "to": "human_escalation",
        "when": "requirements_approval oder concept_approval",
    },
    {
        "from": "supervisor",
        "to": "concept_builder",
        "when": "kein requirements_contract bzw. Jury fordert Überarbeitung",
    },
    {
        "from": "supervisor",
        "to": "concept_panel_reviewer",
        "when": "Concept-Contract da, Jury-Runde läuft (concept_panel_queue)",
    },
    {
        "from": "supervisor",
        "to": "inventory_manager",
        "when": "Konzept freigegeben, noch kein stock_and_tool_context (wenn enabled)",
    },
    {
        "from": "supervisor",
        "to": "fertigung_specialist",
        "when": "Inventar da, Fertigung noch nicht bewertet (manufacturing_assessed)",
    },
    {
        "from": "supervisor",
        "to": "builder_3d",
        "when": "Fertigungsplan/V&V-manufacturing da, noch kein generated_code",
    },
    {
        "from": "supervisor",
        "to": "validator",
        "when": "Code vorhanden, noch kein sandbox_result",
    },
    {
        "from": "supervisor",
        "to": "montage_manager",
        "when": "alle parts in completed_parts, V&V manufacturing abgestimmt, noch nicht montage_assessed",
    },
    {
        "from": "supervisor",
        "to": "END",
        "when": "alle parts fertig und Montage-Prüfung abgeschlossen (montage_assessed)",
    },
    {
        "from": "flexible_specialist",
        "to": "supervisor",
        "when": "Profil + Advisory-Notes gesetzt",
    },
    {
        "from": "interior_architect",
        "to": "supervisor",
        "when": "Raumbrief + suggested_views gesetzt",
    },
    {
        "from": "custom_agent_1",
        "to": "supervisor",
        "when": "Guidance ausgeführt bzw. leer übersprungen",
    },
    {
        "from": "custom_agent_2",
        "to": "supervisor",
        "when": "Guidance ausgeführt bzw. leer übersprungen",
    },
    {
        "from": "vv_manager",
        "to": "supervisor",
        "when": "Requirements aktualisiert (ggf. Alignment-Flag)",
    },
    {
        "from": "concept_builder",
        "to": "supervisor",
        "when": "Contract/Sketch erzeugt → zurück zum Router",
    },
    {
        "from": "concept_critic",
        "to": "supervisor",
        "when": "Kohärenz-Kritik fertig → zurück zum Router / Jury",
    },
    {
        "from": "concept_panel_reviewer",
        "to": "supervisor",
        "when": "Jury-Note erfasst → nächster Prüfer oder Auswertung",
    },
    {
        "from": "inventory_manager",
        "to": "supervisor",
        "when": "CONTEXT_INJECTION fertig",
    },
    {
        "from": "fertigung_specialist",
        "to": "supervisor",
        "when": "Machbarkeit + Arbeitsschritte dokumentiert",
    },
    {
        "from": "montage_manager",
        "to": "supervisor",
        "when": "Passung/Montierbarkeit geprüft, assembly_plan gesetzt",
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
        "role": "Zentraler Orchestrator und Router; stellt das Flexible-Specialist-Profil zu.",
        "responsibilities": [
            "Entscheidet den nächsten Sub-Agenten anhand des States",
            "Orchestriert Flexible → V&V → Concept → Design-V&V → Inventory → Fertigung → Manufacturing-V&V → 3D → Montage",
            "Sichert fertige Teile in completed_parts und rückt zum nächsten Teil vor",
        ],
        "inputs": ["gesamter AgentState"],
        "outputs": ["Routing-Entscheidung", "completed_parts", "iteration_count"],
        "properties": {
            "guidance": "",
            "notes": "Routing ist codegesteuert; guidance dient der Meta-Coach-Dokumentation.",
        },
    },
    "flexible_specialist": {
        "display_name": "Flexible Specialist",
        "role": "Anfragespezifischer Experte – Profil vom Supervisor zugeschnitten, berät von Anfang an.",
        "responsibilities": [
            "Erstellt ein auf die User-Anfrage zugeschnittenes Expertenprofil",
            "Berät V&V, Concept Builder, Inventory und Fertigung",
            "Schreibt advisory_notes und konkrete Vorschläge in den State",
        ],
        "inputs": ["user_prompt", "Supervisor-Guidance"],
        "outputs": ["flexible_specialist_profile", "advisory_notes", "flexible_advice"],
        "properties": {
            "guidance": "",
            "notes": "Optional; wenn deaktiviert, setzt der Supervisor flexible_consulted und überspringt.",
        },
    },
    "interior_architect": {
        "display_name": "Innenarchitekt",
        "role": "Plant Raumkonzept, Visualisierungs-Ansichten und optionalen Grundriss vor dem Concept Builder.",
        "responsibilities": [
            "Erkennt Zimmer-/Raumkonzepte vs. Einzelteile",
            "Schlägt Übersicht, Teil-Ansichten und optional Grundriss vor",
            "Schreibt spatial_notes und advice_for_concept für Concept Builder / Bildgenerierung",
        ],
        "inputs": ["user_prompt", "advisory_notes"],
        "outputs": ["interior_brief", "interior_consulted", "advisory_notes"],
        "properties": {
            "guidance": "",
            "notes": "Optional; deaktiviert → Concept Builder nutzt Heuristik für Ansichten.",
        },
    },
    "concept_critic": {
        "display_name": "Konzept-Kritiker",
        "role": "Schonungslose Prüfung des ausgearbeiteten Konzepts gegen Fotos, Prompt und V&V.",
        "responsibilities": [
            "Deckt Widersprüche zwischen User-Text, Referenzbildern und Concept-Contract auf",
            "Benannte Logiklücken und fehlende Informationen",
            "Sitzt in der Konzept-Jury (Schulnoten-Konsens)",
        ],
        "inputs": [
            "user_prompt",
            "requirements_contract",
            "reference_vision_brief",
            "vv_requirements",
            "coherence_critique",
        ],
        "outputs": ["coherence_critique", "concept_critiqued", "escalation_reason"],
        "properties": {
            "guidance": "",
            "notes": "Jury-Mitglied; Freigabe über Konzept-Konsens-Panel.",
        },
    },
    "concept_panel_reviewer": {
        "display_name": "Konzept-Jury",
        "role": "Sequenzieller Schulnoten-Konsens der Jury-Rollen vor User-Freigabe.",
        "responsibilities": [
            "Bewertet das Konzept aus der Perspektive der jeweils zugewiesenen Jury-Rolle",
            "Vergibt Note 1–6; Mangelhaft (>=5) verhindert Freigabe",
            "Liefert Verbesserungsauflagen für den Concept Builder",
        ],
        "inputs": [
            "requirements_contract",
            "design_spec",
            "reference_vision_brief",
            "concept_image_urls",
            "panel_reviewer_id",
        ],
        "outputs": ["concept_panel_grades", "concept_panel_queue", "panel_reviewer_id"],
        "properties": {
            "guidance": "",
            "notes": "Fixed Node; Jury-Mitglieder kommen aus enabled Agents (Flex/Interior/V&V/Critic/Fertigung).",
        },
    },
    "custom_agent_1": {
        "display_name": "Leer-Agent 1",
        "role": "Leerer Slot – Rolle ausschließlich über manuelle Guidance.",
        "responsibilities": [
            "Führt nur die hinterlegte Guidance aus",
            "Schreibt Ergebnis in advisory_notes für nachfolgende Agenten",
        ],
        "inputs": ["user_prompt", "guidance", "advisory_notes"],
        "outputs": ["advisory_notes", "empty_agents_consulted"],
        "properties": {
            "guidance": "",
            "notes": "In der Standard-Konfiguration deaktiviert. Guidance im Agent Workflow setzen, dann aktivieren.",
        },
    },
    "custom_agent_2": {
        "display_name": "Leer-Agent 2",
        "role": "Leerer Slot – Rolle ausschließlich über manuelle Guidance.",
        "responsibilities": [
            "Führt nur die hinterlegte Guidance aus",
            "Schreibt Ergebnis in advisory_notes für nachfolgende Agenten",
        ],
        "inputs": ["user_prompt", "guidance", "advisory_notes"],
        "outputs": ["advisory_notes", "empty_agents_consulted"],
        "properties": {
            "guidance": "",
            "notes": "In der Standard-Konfiguration deaktiviert. Guidance im Agent Workflow setzen, dann aktivieren.",
        },
    },
    "vv_manager": {
        "display_name": "V&V Manager",
        "role": "Verification & Validation – Requirements aus dem Prompt, phasenweise vertieft.",
        "responsibilities": [
            "High-Level-Requirements in der Konzeptphase",
            "Vertiefung in Design-, 3D- und Fertigungsphase",
            "Abstimmung mit dem Nutzer bei kritischen offenen Fragen",
            "Nutzt Input von Concept, Fertigung, Inventory und Flexible Specialist",
        ],
        "inputs": [
            "user_prompt",
            "advisory_notes",
            "requirements_contract",
            "manufacturing_plan",
            "flexible_specialist_profile",
        ],
        "outputs": ["vv_requirements", "vv_phase", "vv_needs_alignment", "vv_consulted_phases"],
        "properties": {
            "guidance": "",
            "notes": "needs_user_alignment nur bei echten open_questions → requirements_approval.",
        },
    },
    "concept_builder": {
        "display_name": "Concept Builder",
        "role": "Zerlegt Nutzerwünsche in CNC-fertigbare Teile und erzeugt den Requirements-Contract.",
        "responsibilities": [
            "Mehrteil-Dekomposition (LLM oder Heuristik) unter Beachtung der V&V-Requirements",
            "2D-Sketch / Konzeptfoto für Human-Approval",
            "gap_analysis und Annahmen dokumentieren",
        ],
        "inputs": ["user_prompt", "vv_requirements", "advisory_notes", "refinement_request"],
        "outputs": ["requirements_contract", "concept_sketch_svg", "concept_image_url", "concept_image_urls"],
        "properties": {
            "guidance": "",
            "prefer_conservative_dimensions": True,
            "notes": "Nutzt interior_brief für Galerie (Übersicht/Teile/Grundriss), max. 5 Bilder.",
        },
    },
    "inventory_manager": {
        "display_name": "Inventory Specialist",
        "role": "Passt Contract an verfügbares Material/Werkzeug an; nutzt DB + Knowledge Graph.",
        "responsibilities": [
            "Stock- und Tool-Matching direkt gegen die Inventar-DB",
            "Abfrage des Inventory Knowledge Graphs (gelernte Assoziationen)",
            "Nach Matches: Graph-Kanten verstärken (Lernfähigkeit)",
            "Lessons-Learned-Regeln laden",
            "CONTEXT_INJECTION für Fertigung und 3D Builder",
        ],
        "inputs": [
            "requirements_contract",
            "Inventar-DB",
            "inventory_knowledge_graph.json",
            "Asset-Beschreibungen (notes)",
            "rules/*.json",
        ],
        "outputs": ["stock_and_tool_context", "ggf. angepasster Contract", "KG-Lern-Update"],
        "properties": {
            "guidance": "",
            "strict_tool_match": False,
            "notes": (
                "Optional zuschaltbar. Graph wird per POST /inventory/knowledge-graph/train "
                "aus der DB aufgebaut; Matches verstärken learned edges."
            ),
        },
    },
    "fertigung_specialist": {
        "display_name": "Fertigungs Specialist",
        "role": "Experte für alle Maschinen in der Inventar-DB; Machbarkeit und Arbeitsabläufe.",
        "responsibilities": [
            "Bewertet Design-Konzepte auf Realisierbarkeit in der Werkstatt",
            "Wählt benötigte Maschinen aus dem Inventar",
            "Erstellt Schritt-für-Schritt-Arbeitsabläufe",
        ],
        "inputs": [
            "requirements_contract",
            "stock_and_tool_context",
            "vv_requirements",
            "advisory_notes",
            "Maschinen/Assets aus Inventar-DB",
        ],
        "outputs": ["manufacturing_plan", "manufacturing_feasibility", "manufacturing_assessed"],
        "properties": {
            "guidance": "",
            "notes": "Nutzt Tool-Tabelle und maschinenbezogene Assets aus der Inventar-DB.",
        },
    },
    "montage_manager": {
        "display_name": "Montage Manager",
        "role": "Prüft Passung/Montierbarkeit und schreibt die Montage-Anleitung inkl. Werkzeugwahl.",
        "responsibilities": [
            "Vergleicht Maße und Anschlussflächen der completed_parts",
            "Bewertet Passung und Montierbarkeit (assemblable)",
            "Schreibt eine praxisnahe Montage-Anleitung (Schritte, Sicherheit, Checkliste)",
            "Nutzt vorhandene Werkzeuge aus der Inventar-DB; empfiehlt fehlende Neuanschaffungen",
        ],
        "inputs": [
            "completed_parts",
            "requirements_contract",
            "manufacturing_plan",
            "vv_requirements",
            "advisory_notes",
            "Tools/Assets aus Inventar-DB",
        ],
        "outputs": [
            "montage_result",
            "assembly_plan",
            "assembly_manual",
            "montage_assessed",
        ],
        "properties": {
            "guidance": "",
            "notes": (
                "Läuft nach Abschluss aller Teile und Manufacturing-V&V, vor END. "
                "assembly_manual enthält Markdown-Anleitung plus tools_from_inventory / tools_recommended."
            ),
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
        "role": "Pausiert den Graphen (interrupt) für Requirements- und Konzept-Freigabe.",
        "responsibilities": [
            "Requirements-Abstimmung (requirements_approval)",
            "Konzept-Freigabe (concept_approval)",
            "Sonstige Eskalationen ohne User-Pause fortsetzen",
        ],
        "inputs": ["escalation_reason", "vv_requirements", "concept_*", "requirements_contract"],
        "outputs": ["vv_approved", "concept_approved", "refinement_request", "User-Decision"],
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
        "Hub-and-Spoke mit V&V-Phasen: Flexible Specialist → V&V (concept) → Concept Builder → "
        "V&V (design) → Inventory Specialist → Fertigungs Specialist → V&V (manufacturing) → "
        "3D Builder → Validator → Montage Manager. Korrekturschleifen über refinement_request.target."
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
        raw_edges = raw.get("edges")
        edge_nodes: set[Any] = set()
        if isinstance(raw_edges, list):
            for e in raw_edges:
                if isinstance(e, dict):
                    edge_nodes.add(e.get("from"))
                    edge_nodes.add(e.get("to"))
        # Alte Topologie ohne V&V/Flexible/Fertigung/Montage → Defaults
        if not raw_edges or "vv_manager" not in edge_nodes or "montage_manager" not in edge_nodes or "custom_agent_1" not in edge_nodes or "concept_critic" not in edge_nodes:
            edges = deepcopy(DEFAULT_EDGES)
        else:
            edges = raw_edges
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
    try:
        from app.services import agent_workflow_db as wf_db

        active = wf_db.get_active_config()
        agent = (active.get("agents") or {}).get(agent_id) or {}
        props = agent.get("properties") or {}
        guidance = (props.get("guidance") or "").strip()
        if guidance:
            return guidance
    except Exception as exc:  # noqa: BLE001
        logger.debug("Aktive Workflow-DB guidance nicht lesbar: %s", exc)
    profiles = load_agent_profiles()
    agent = (profiles.get("agents") or {}).get(agent_id) or {}
    props = agent.get("properties") or {}
    guidance = (props.get("guidance") or "").strip()
    return guidance


def get_enabled_agents() -> set[str]:
    """Aktive Agenten-IDs laut Workflow-DB (fixe Agenten immer enthalten)."""
    from app.models.agent_workflow_config import FIXED_AGENT_IDS

    try:
        from app.services import agent_workflow_db as wf_db

        active = wf_db.get_active_config()
        enabled = set(active.get("enabled_agents") or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("enabled_agents aus DB nicht lesbar: %s", exc)
        enabled = set(DEFAULT_AGENTS.keys())
    # Neu eingeführte Kern-Agenten: aktiv, bis Meta-Coach sie explizit abschaltet
    # (persistierte Listen ohne den neuen Key würden sie sonst dauerhaft auslassen).
    if "concept_critic" in DEFAULT_AGENTS and "concept_critic" not in enabled:
        enabled.add("concept_critic")
    if "concept_panel_reviewer" in DEFAULT_AGENTS and "concept_panel_reviewer" not in enabled:
        enabled.add("concept_panel_reviewer")
    return enabled | set(FIXED_AGENT_IDS)


def is_agent_enabled(agent_id: str) -> bool:
    from app.models.agent_workflow_config import FIXED_AGENT_IDS

    if agent_id in FIXED_AGENT_IDS:
        return True
    return agent_id in get_enabled_agents()


def get_max_iterations() -> int:
    try:
        from app.services import agent_workflow_db as wf_db

        cfg = wf_db.get_active_config().get("config") or {}
        value = int(cfg.get("max_iterations", settings.agent_max_iterations))
        return max(1, min(value, 50))
    except Exception:  # noqa: BLE001
        pass
    cfg = load_workflow_config()
    try:
        value = int(cfg.get("max_iterations", settings.agent_max_iterations))
        return max(1, min(value, 50))
    except (TypeError, ValueError):
        return settings.agent_max_iterations


def get_sandbox_timeout() -> int:
    try:
        from app.services import agent_workflow_db as wf_db

        cfg = wf_db.get_active_config().get("config") or {}
        value = int(cfg.get("sandbox_timeout_seconds", settings.sandbox_timeout_seconds))
        return max(5, min(value, 300))
    except Exception:  # noqa: BLE001
        pass
    cfg = load_workflow_config()
    try:
        value = int(cfg.get("sandbox_timeout_seconds", settings.sandbox_timeout_seconds))
        return max(5, min(value, 300))
    except (TypeError, ValueError):
        return settings.sandbox_timeout_seconds


def update_agent_property(agent_id: str, property_key: str, value: Any) -> dict[str, Any]:
    """Schreibt in die aktive Workflow-DB-Konfiguration (nicht via Meta-Coach)."""
    from app.services import agent_workflow_db as wf_db

    try:
        return wf_db.update_active_agent_property(agent_id, property_key, value)
    except PermissionError:
        # Fallback: JSON-Datei (Legacy), wenn nur Standard aktiv
        doc = load_agent_profiles()
        agents = doc.setdefault("agents", {})
        if agent_id not in agents:
            raise KeyError(f"Unbekannter Agent: {agent_id}") from None
        props = agents[agent_id].setdefault("properties", {})
        props[property_key] = value
        save_agent_profiles(doc)
        return agents[agent_id]


def update_workflow_keys(updates: dict[str, Any]) -> dict[str, Any]:
    from app.services import agent_workflow_db as wf_db

    allowed = {"max_iterations", "sandbox_timeout_seconds", "description", "notes"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    try:
        row = wf_db.update_active(config=filtered)
        return row.get("config") or filtered
    except PermissionError:
        cfg = load_workflow_config()
        for key, value in filtered.items():
            cfg[key] = value
        save_workflow_config(cfg)
        return cfg


def workflow_overview() -> dict[str, Any]:
    try:
        from app.services import agent_workflow_db as wf_db

        active = wf_db.get_active_config()
        return {
            "agents": active.get("agents") or {},
            "edges": active.get("edges") or [],
            "config": active.get("config") or {},
            "enabled_agents": active.get("enabled_agents") or [],
            "fixed_agents": active.get("fixed_agents") or ["supervisor"],
            "active_config": {
                "id": active.get("id"),
                "name": active.get("name"),
                "is_standard": active.get("is_standard"),
                "description": active.get("description"),
            },
            "profiles_updated_at": active.get("updated_at"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow-Übersicht aus DB fehlgeschlagen: %s", exc)
        profiles = load_agent_profiles()
        config = load_workflow_config()
        return {
            "agents": profiles.get("agents", {}),
            "edges": profiles.get("edges", []),
            "config": config,
            "enabled_agents": [a for a in DEFAULT_AGENTS if a not in {"custom_agent_1", "custom_agent_2"}],
            "fixed_agents": ["supervisor"],
            "active_config": None,
            "profiles_updated_at": profiles.get("updated_at"),
        }
