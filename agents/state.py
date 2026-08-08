"""LangGraph State Definition – Agenten-Topologie (SPEC Kap. 3.1, 3.3, 3.6)."""

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

from agents.transcript import append_transcript


class RefinementRequest(TypedDict, total=False):
    """Rücksprung-/Korrekturanfrage zwischen Agenten (SPEC Kap. 3.4 Loop-Mechanik).

    `reason == "concept_feedback"` markiert eine Nutzer-Rückmeldung auf den
    2D-Entwurf (Freigabe-Gate, Nutzer-Feedback zum Chapter-3-Frontend); der
    Freitext dazu steht in `feedback`. Alle anderen `reason`-Werte betreffen
    die bestehenden autonomen Korrekturschleifen für das aktuell in Arbeit
    befindliche Teil (`current_part_index`)."""

    from_agent: str
    target: str
    reason: str
    feedback: str | None


class SandboxResult(TypedDict, total=False):
    """Ergebnis eines Sandbox-Laufs (SPEC Kap. 1.6, 2.3, 4)."""

    status: Literal["SUCCESS", "SANDBOX_ERROR"]
    error_type: str | None
    error_message: str | None
    traceback: str | None
    stdout: str | None
    export_paths: list[str]


class AgentState(TypedDict, total=False):
    # --- Kernfelder ---
    # `requirements_contract` hat die Form
    # {"project_title": str, "raw_prompt": str, "parts": [<part>, ...]},
    # wobei jedes <part> die bisherige Einzel-Contract-Struktur
    # (functional_geometry/material_tool_constraints/manufacturing_features/
    # gap_analysis) plus `name` und optional `position_xy_mm` trägt
    # (Nutzer-Feedback: Mehrteil-Anfragen wie ein Zimmer-Layout werden vom
    # Concept Builder in eine sequenziell abzuarbeitende Teile-Liste
    # zerlegt, Kap. 3.3).
    user_prompt: str
    requirements_contract: dict[str, Any]
    stock_and_tool_context: dict[str, Any]
    generated_code: str
    sandbox_result: SandboxResult
    iteration_count: int
    current_agent: str
    messages: Annotated[list[Any], add_messages]
    human_approval_required: bool

    # --- Erweiterungen für Routing, Eskalation & Meta-Coach-Logging ---
    session_id: str
    refinement_request: RefinementRequest | None
    escalation_reason: str | None
    error_history: list[str]

    # --- Konzept-Freigabe & Mehrteil-Ausarbeitung (Nutzer-Feedback) ---
    concept_sketch_svg: str | None  # technische Draufsicht (Fallback / eingeklappt)
    concept_image_url: str | None  # fotorealistisches Raumfoto (OpenAI Images)
    concept_approved: bool
    current_part_index: int
    completed_parts: list[dict[str, Any]]

    # --- Analyse-Log: Unterhaltungsverläufe der Sub-Agenten ---
    agent_transcript: Annotated[list[dict[str, Any]], append_transcript]
