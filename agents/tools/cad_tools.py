"""LangGraph-Tool zur Sandbox-Ausführung von build123d-Code (SPEC Kap. 1.6, 4).

`execute_build123d_code` ist der zentrale Übergabepunkt zwischen dem 3D-
Builder/Validator und dem Sandbox Execution Service. Primär wird der echte,
netzwerkisolierte Docker-Sibling-Container genutzt
(`app.services.sandbox_runner`); ist der Docker-Socket nicht verfügbar
(z. B. lokale Entwicklung ohne gemounteten `/var/run/docker.sock`), wird
transparent auf den Subprozess-Fallback (`agents.sandbox_client`,
reduzierte Isolation) zurückgefallen.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from app.services.sandbox_runner import ExecutionResult, is_docker_sandbox_available, run_sandboxed

logger = logging.getLogger(__name__)


def _execution_result_to_dict(result: ExecutionResult) -> dict:
    return {
        "success": result.success,
        "stdout": result.stdout,
        "error_traceback": result.error_traceback,
        "export_paths": result.export_paths,
    }


def _run_fallback_subprocess(code: str, timeout_seconds: int) -> dict:
    from agents.sandbox_client import run_in_sandbox

    legacy = run_in_sandbox(code, timeout_seconds=timeout_seconds)
    if legacy.get("status") == "SUCCESS":
        return {
            "success": True,
            "stdout": legacy.get("stdout", ""),
            "error_traceback": None,
            "export_paths": legacy.get("export_paths", []),
        }
    return {
        "success": False,
        "stdout": legacy.get("stdout", ""),
        "error_traceback": legacy.get("traceback")
        or f"{legacy.get('error_type')}: {legacy.get('error_message')}",
        "export_paths": legacy.get("export_paths", []),
    }


def execute_build123d_code(code: str, timeout_seconds: int = 20) -> dict:
    """Führt generierten build123d-Python-Code aus und gibt ein Dict mit
    `success`, `stdout`, `error_traceback` und `export_paths` zurück.

    Plain-Function-Variante für die direkte Verwendung in Graph-Nodes; siehe
    `execute_build123d_code_tool` für die an LangChain/LangGraph `bind_tools()`
    anbindbare Tool-Variante mit identischer Logik.
    """
    if is_docker_sandbox_available():
        result = run_sandboxed(code, timeout_seconds=timeout_seconds)
        return _execution_result_to_dict(result)

    logger.warning(
        "Docker-Sandbox nicht verfügbar – Fallback auf lokalen Subprozess (reduzierte Isolation, "
        "kein --network none / --read-only)."
    )
    return _run_fallback_subprocess(code, timeout_seconds)


@tool
def execute_build123d_code_tool(code: str, timeout_seconds: int = 20) -> dict:
    """Führt generierten build123d-Python-Code in einem isolierten, ephemeren
    Docker-Sandbox-Container aus (netzwerkisoliert, Read-Only-Filesystem,
    CPU-/RAM-Limits, Timeout) und gibt Erfolg, stdout, Fehler-Traceback und
    Pfade der exportierten STEP-/STL-Dateien zurück."""
    return execute_build123d_code(code, timeout_seconds=timeout_seconds)
