"""Validator – Sandbox-Ausführung, Fehlerklassifizierung & Meta-Coach-Logging
(SPEC Kap. 1.6, 2.3, 3.2.5, 3.4 Loop 3, Kap. 4)."""

import logging
import uuid
from datetime import datetime, timezone

from agents.state import AgentState
from agents.tools.cad_tools import execute_build123d_code
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_sandbox_timeout
from app.services.sandbox_runner import parse_error_summary

logger = logging.getLogger(__name__)

CODE_ERROR_TYPES = {
    "SyntaxError", "NameError", "TypeError", "AttributeError",
    "ImportError", "IndentationError", "ModuleNotFoundError",
}


def _classify_error_target(error_type: str | None) -> str:
    """Code-Fehler -> 3D Builder (schnelle lokale Korrektur), sonst -> Concept Builder (Design)."""
    if error_type in CODE_ERROR_TYPES:
        return "builder_3d"
    return "concept_builder"


def _to_sandbox_result(execution: dict) -> dict:
    """Adaptiert das ExecutionResult-Dict des Sandbox Execution Service (Kap. 4:
    success/stdout/error_traceback/export_paths) auf das im State verwendete
    SandboxResult-Schema (status/error_type/error_message/traceback/...)."""
    if execution.get("success"):
        return {
            "status": "SUCCESS",
            "error_type": None,
            "error_message": None,
            "traceback": None,
            "stdout": execution.get("stdout", ""),
            "export_paths": execution.get("export_paths", []),
        }

    error_type, error_message = parse_error_summary(execution.get("error_traceback"))
    return {
        "status": "SANDBOX_ERROR",
        "error_type": error_type,
        "error_message": error_message or "Unbekannter Sandbox-Fehler",
        "traceback": execution.get("error_traceback"),
        "stdout": execution.get("stdout", ""),
        "export_paths": [],
    }


def _log_execution(state: AgentState, result: dict) -> None:
    """Schreibt einen strukturierten Log-Eintrag für den Meta-Coach (Kap. 2.3, 2.4)."""
    try:
        from app.db.postgres import SessionLocal
        from app.models.execution_log import ExecutionLog

        db = SessionLocal()
        try:
            log_payload = {
                "log_id": f"log_{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H%M%S_%f')}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context": state.get("requirements_contract", {}),
                "prompt": state.get("user_prompt"),
                "generated_code": state.get("generated_code"),
                "execution_result": result,
            }
            session_raw = state.get("session_id")
            session_uuid = uuid.UUID(session_raw) if session_raw else uuid.uuid4()
            db.add(
                ExecutionLog(
                    session_id=session_uuid,
                    prompt=state.get("user_prompt"),
                    generated_code=state.get("generated_code"),
                    sandbox_success=result.get("status") == "SUCCESS",
                    error_message=result.get("error_message"),
                    log_payload=log_payload,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - Logging darf den Workflow nie unterbrechen
        logger.warning("Konnte execution_log nicht persistieren: %s", exc)


def validator_node(state: AgentState) -> AgentState:
    parts = (state.get("requirements_contract") or {}).get("parts", [])
    idx = state.get("current_part_index", 0)
    part_name = parts[idx].get("name", "Unbenannt") if idx < len(parts) else "Unbenannt"
    part_label = f"Teil {idx + 1}/{len(parts)} ('{part_name}')"

    code = state.get("generated_code") or ""
    execution = execute_build123d_code(code, timeout_seconds=get_sandbox_timeout())
    result = _to_sandbox_result(execution)
    _log_execution(state, result)

    if result.get("status") == "SUCCESS":
        export_note = (
            f" Exportiert: {', '.join(result['export_paths'])}" if result.get("export_paths") else ""
        )
        return {
            "sandbox_result": result,
            "error_history": [],
            "refinement_request": None,
            "current_agent": "validator",
            "messages": [
                {
                    "role": "assistant",
                    "content": f"[Validator] {part_label}: Sandbox-Ausführung erfolgreich.{export_note}",
                }
            ],
            "agent_transcript": [
                make_entry(
                    "validator",
                    "sandbox_success",
                    f"{part_label}: Sandbox OK{export_note}",
                    detail={
                        "part_label": part_label,
                        "export_paths": result.get("export_paths"),
                        "stdout": truncate(result.get("stdout"), 400),
                    },
                    to_agent="supervisor",
                )
            ],
        }

    error_history = list(state.get("error_history", []))
    error_message = result.get("error_message") or "Unbekannter Fehler"
    error_history.append(error_message)

    target = _classify_error_target(result.get("error_type"))
    repeated_failure = len(error_history) >= 2 and error_history[-1] == error_history[-2]

    updates: AgentState = {
        "sandbox_result": result,
        "error_history": error_history,
        "current_agent": "validator",
        "messages": [
            {
                "role": "assistant",
                "content": f"[Validator] {part_label}: Sandbox-Fehler ({result.get('error_type')}): {error_message}",
            }
        ],
        "agent_transcript": [
            make_entry(
                "validator",
                "sandbox_error",
                f"{part_label}: {result.get('error_type')}: {error_message}",
                detail={
                    "part_label": part_label,
                    "error_type": result.get("error_type"),
                    "error_message": error_message,
                    "traceback": truncate(result.get("traceback"), 1000),
                    "target": target,
                    "repeated_failure": repeated_failure,
                },
                to_agent=target,
            )
        ],
    }

    # Immer weiterarbeiten: auch bei wiederholtem Fehler kein User-Interrupt
    updates["refinement_request"] = {"from_agent": "validator", "target": target, "reason": error_message}
    if repeated_failure:
        updates["messages"] = list(updates["messages"]) + [
            {
                "role": "assistant",
                "content": (
                    f"[Validator] {part_label}: Wiederholter Fehler – "
                    f"versuche erneut über {target} (keine User-Eskalation)."
                ),
            }
        ]
        updates["agent_transcript"] = list(updates["agent_transcript"]) + [
            make_entry(
                "validator",
                "retry_without_escalation",
                f"Wiederholter Fehler → weiter an {target}",
                detail={"error_message": error_message, "target": target},
                to_agent=target,
            )
        ]

    return updates
