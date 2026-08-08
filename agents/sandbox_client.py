"""Legacy-Fallback-Sandbox-Client – führt sandbox/run.py per Subprozess aus
(SPEC Kap. 1.6, 3.2.5).

Seit Kap. 4 ist `app.services.sandbox_runner` (echter, isolierter Docker-
Sibling-Container über den Docker-Socket) der PRIMÄRE Ausführungsweg; siehe
`agents/tools/cad_tools.py`. Dieses Modul dient nur noch als Fallback, falls
der Docker-Socket nicht verfügbar ist (z. B. lokale Entwicklung ohne
gemounteten `/var/run/docker.sock`) – mit reduzierter Isolation, da der Code
dann im selben Container-Namespace wie die API läuft (kein `--network none`,
kein separates Read-Only-Root-Filesystem).
"""

import json
import logging
import subprocess
import sys

from app.config import settings

logger = logging.getLogger(__name__)


def run_in_sandbox(code: str, timeout_seconds: int | None = None) -> dict:
    """Führt `code` isoliert über sandbox/run.py aus und gibt das JSON-Ergebnis zurück."""

    timeout = timeout_seconds or settings.sandbox_timeout_seconds

    try:
        completed = subprocess.run(
            # "-" erzwingt stdin-Modus in run.py; export_dir wird explizit übergeben,
            # da der Default (/exports) im API-Container nicht existiert.
            [sys.executable, settings.sandbox_script_path, "-", settings.exports_dir],
            input=code,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "SANDBOX_ERROR",
            "error_type": "TimeoutError",
            "error_message": f"Sandbox-Ausführung nach {timeout}s abgebrochen (Timeout).",
            "traceback": None,
        }
    except FileNotFoundError as exc:
        logger.error("Sandbox-Skript nicht gefunden unter %s: %s", settings.sandbox_script_path, exc)
        return {
            "status": "SANDBOX_ERROR",
            "error_type": "SandboxUnavailable",
            "error_message": f"Sandbox-Skript nicht gefunden unter {settings.sandbox_script_path}.",
            "traceback": None,
        }

    stdout = completed.stdout.strip()
    if not stdout:
        return {
            "status": "SANDBOX_ERROR",
            "error_type": "EmptySandboxOutput",
            "error_message": completed.stderr.strip() or "Sandbox lieferte keine Ausgabe.",
            "traceback": completed.stderr,
        }

    try:
        return json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return {
            "status": "SANDBOX_ERROR",
            "error_type": "MalformedSandboxOutput",
            "error_message": stdout,
            "traceback": completed.stderr,
        }
