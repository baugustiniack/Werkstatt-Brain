"""Leer-Agent – Rolle ausschließlich über Meta-Coach-/User-Guidance.

Zwei Instanzen (custom_agent_1 / custom_agent_2) können im Workflow aktiviert
und manuell per guidance gesteuert werden. In der Standard-Konfig sind sie aus.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agents.llm_client import call_llm_text, is_llm_configured
from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_agent_guidance

logger = logging.getLogger(__name__)


def _run_empty_agent(state: AgentState, agent_id: str, display: str) -> AgentState:
    guidance = (get_agent_guidance(agent_id) or "").strip()
    prompt = (state.get("user_prompt") or "")[:1200]
    contract = state.get("requirements_contract") or {}
    advisory = state.get("advisory_notes") or ""
    consulted = list(state.get("empty_agents_consulted") or [])

    if not guidance:
        note = (
            f"{display}: keine Guidance gesetzt – überspringe inhaltliche Arbeit "
            "(bitte Guidance im Agent Workflow hinterlegen)."
        )
        output = ""
    elif is_llm_configured():
        try:
            output = call_llm_text(
                (
                    f"Du bist „{display}“ im Werkstatt-Brain Multi-Agenten-System.\n"
                    "Deine gesamte Rolle und Aufgabe ergibt sich NUR aus der folgenden Guidance.\n"
                    "Antworte auf Deutsch, konkret und kurz (kein JSON, außer die Guidance verlangt es).\n\n"
                    f"## Guidance\n{guidance}"
                ),
                (
                    f"User-Prompt:\n{prompt}\n\n"
                    f"Projekt: {contract.get('project_title') or '—'}\n"
                    f"Bestehende Advisory-Notes:\n{advisory or '(keine)'}\n"
                ),
                max_tokens=1800,
            )
            note = f"{display}: Guidance ausgeführt ({len(output)} Zeichen)."
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s LLM fehlgeschlagen: %s", agent_id, exc)
            output = ""
            note = f"{display}: LLM fehlgeschlagen – {exc}"
    else:
        output = f"[Ohne LLM] Guidance notiert: {guidance[:500]}"
        note = f"{display}: kein LLM – Guidance nur protokolliert."

    if agent_id not in consulted:
        consulted.append(agent_id)

    # Guidance-Output an advisory_notes anhängen (für nachfolgende Agenten)
    merged_advisory = advisory
    if output:
        block = f"\n\n[{display}]\n{output.strip()}"
        merged_advisory = (advisory + block).strip() if advisory else f"[{display}]\n{output.strip()}"

    return {
        "empty_agents_consulted": consulted,
        "advisory_notes": merged_advisory,
        "current_agent": agent_id,
        "messages": [{"role": "assistant", "content": f"[{display}] {note}"}],
        "agent_transcript": [
            make_entry(
                agent_id,
                "guidance_run",
                note,
                detail={
                    "has_guidance": bool(guidance),
                    "guidance_preview": truncate(guidance, 400),
                    "output_preview": truncate(output, 800),
                },
                to_agent="supervisor",
            )
        ],
    }


def make_empty_agent_node(agent_id: str, display_name: str) -> Callable[[AgentState], AgentState]:
    def _node(state: AgentState) -> AgentState:
        return _run_empty_agent(state, agent_id, display_name)

    _node.__name__ = f"{agent_id}_node"
    return _node


custom_agent_1_node = make_empty_agent_node("custom_agent_1", "Leer-Agent 1")
custom_agent_2_node = make_empty_agent_node("custom_agent_2", "Leer-Agent 2")
