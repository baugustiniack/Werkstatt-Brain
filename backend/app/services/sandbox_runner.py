"""Sandbox Execution Service – startet ephemere, isolierte Docker-Container zur
sicheren Ausführung von generiertem build123d/cadquery-Code (SPEC Kap. 1.6, 4).

Isolations-Maßnahmen pro Ausführung (SPEC Kap. 1.6):
    - network_mode="none"        : keine Netzwerkverbindung möglich
    - read_only=True             : Root-Dateisystem des Containers unveränderlich
    - cap_drop=["ALL"]           : keine Linux-Capabilities
    - security_opt no-new-privileges
    - mem_limit / nano_cpus / pids_limit : Resource-Limits
    - Timeout mit anschließendem Kill

Docker-outside-of-Docker (Sibling-Container-Pattern):
    Der API-Container hat den Docker-Socket des Hosts gemountet
    (/var/run/docker.sock, siehe docker-compose.yml) und kann darüber
    "Sibling"-Container auf dem Host starten. Damit der ephemere Sandbox-
    Container Zugriff auf das auszuführende Skript und den Export-Ordner
    erhält, werden NICHT Host-Pfade bind-gemountet (aus einem Container
    heraus – insbesondere unter Windows/Docker Desktop – nicht zuverlässig
    auflösbar), sondern per `volumes_from` exakt die bereits im API-
    Container deklarierten Volumes (`/app/sandbox_workspace`, `/app/exports`)
    übernommen. Das ist der Standard-Idiom für dieses Szenario und
    funktioniert plattformunabhängig ohne Host-Pfad-Kenntnis.

Sicherheitshinweis: Das Mounten des Docker-Sockets in den API-Container
gewährt diesem effektiv volle Kontrolle über den Docker-Daemon des Hosts
(Root-Äquivalent). Dies ist ein bewusster, in Kap. 4 explizit geforderter
Trade-off für Phase 1 (Entwicklungs-Laptop). Für den produktiven Werkstatt-
Server (Phase 2, Kap. 1.2) wird empfohlen, den Zugriff über einen
`docker-socket-proxy` (nur erlaubte Endpunkte: create/start/wait/logs/kill/
remove für Container) einzuschränken.
"""

from __future__ import annotations

import logging
import re
import shutil
import socket
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_LAST_LINE_ERROR_PATTERN = re.compile(r"^([\w.]+)\s*:\s*(.*)$")


@dataclass
class ExecutionResult:
    """Strukturiertes Ergebnis einer Sandbox-Ausführung (SPEC Kap. 4)."""

    success: bool
    stdout: str = ""
    error_traceback: str | None = None
    export_paths: list[str] = field(default_factory=list)


def _resolve_source_container() -> str:
    """Ermittelt den Container, dessen Volumes über `volumes_from` geteilt werden."""
    return settings.api_container_name or socket.gethostname()


def is_docker_sandbox_available() -> bool:
    """Prüft, ob der Docker-Socket erreichbar UND das Sandbox-Image gebaut ist."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.images.get(settings.sandbox_docker_image)
        return True
    except Exception as exc:  # noqa: BLE001 - jede Nichtverfügbarkeit -> Fallback
        logger.info(
            "Docker-Sandbox nicht verfügbar (Image '%s'): %s", settings.sandbox_docker_image, exc
        )
        return False


def parse_error_summary(traceback_text: str | None) -> tuple[str | None, str | None]:
    """Extrahiert (error_type, error_message) aus der letzten Traceback-Zeile,
    z. B. "ValueError: Radius exceeds face boundary" -> ("ValueError", "Radius exceeds face boundary")."""
    if not traceback_text:
        return None, None

    lines = [ln for ln in traceback_text.strip().splitlines() if ln.strip()]
    if not lines:
        return None, traceback_text.strip() or None

    match = _LAST_LINE_ERROR_PATTERN.match(lines[-1])
    if match:
        return match.group(1).split(".")[-1], match.group(2).strip() or lines[-1]
    return "UnknownError", lines[-1]


def run_sandboxed(code: str, timeout_seconds: int | None = None) -> ExecutionResult:
    """Führt `code` in einem ephemeren, isolierten Docker-Container aus (SPEC Kap. 1.6, 4).

    Fällt bei jeglicher Infrastrukturstörung (Docker-Socket nicht erreichbar,
    Image fehlt, Timeout, ...) auf ein strukturiertes Fehlerergebnis zurück,
    statt eine Exception zu werfen – der Aufrufer (Validator) entscheidet dann
    über die weitere Korrekturschleife.
    """
    import docker
    from docker.errors import DockerException, NotFound
    from docker.types import Ulimit
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from requests.exceptions import ReadTimeout

    timeout = timeout_seconds or settings.sandbox_timeout_seconds
    run_id = uuid.uuid4().hex[:12]

    workspace_dir = Path(settings.sandbox_workspace_dir) / run_id
    export_dir = Path(settings.exports_dir) / run_id

    try:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        export_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "script.py").write_text(code, encoding="utf-8")
    except OSError as exc:
        return ExecutionResult(success=False, error_traceback=f"SandboxInfrastructureError: {exc}")

    container_script_path = f"{settings.sandbox_workspace_dir}/{run_id}/script.py"
    container_export_dir = f"{settings.exports_dir}/{run_id}"

    client = None
    container = None
    try:
        client = docker.from_env()

        container = client.containers.run(
            settings.sandbox_docker_image,
            command=[container_script_path, container_export_dir],
            volumes_from=[_resolve_source_container()],
            network_mode="none",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=settings.sandbox_memory_limit,
            nano_cpus=int(settings.sandbox_cpu_limit * 1_000_000_000),
            pids_limit=64,
            tmpfs={"/tmp": "size=64m,noexec"},
            ulimits=[Ulimit(name="nofile", soft=256, hard=256)],
            environment={"HOME": "/tmp", "XDG_CACHE_HOME": "/tmp", "MPLCONFIGDIR": "/tmp"},
            detach=True,
        )

        timed_out = False
        try:
            exit_status = container.wait(timeout=timeout)
            exit_code = exit_status.get("StatusCode", 1)
        except (ReadTimeout, RequestsConnectionError):
            timed_out = True
            exit_code = -1
            try:
                container.kill()
            except Exception:  # noqa: BLE001
                pass

        logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")

        if timed_out:
            return ExecutionResult(
                success=False,
                stdout=logs,
                error_traceback=f"TimeoutError: Sandbox-Ausführung nach {timeout}s abgebrochen (Timeout).",
            )

        return _parse_container_output(logs, exit_code, export_dir)

    except DockerException as exc:
        logger.error("Docker-Sandbox-Infrastrukturfehler: %s", exc)
        return ExecutionResult(success=False, error_traceback=f"SandboxInfrastructureError: {exc}")
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except NotFound:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("Konnte Sandbox-Container nicht entfernen: %s", exc)
        shutil.rmtree(workspace_dir, ignore_errors=True)


def _parse_container_output(logs: str, exit_code: int, export_dir: Path) -> ExecutionResult:
    """Parst die letzte JSON-Zeile aus sandbox/run.py; Export-Pfade werden zusätzlich
    per Verzeichnis-Scan verifiziert (robust gegen abweichende/fehlende JSON-Ausgabe)."""
    import json

    lines = [ln for ln in logs.strip().splitlines() if ln.strip()]
    payload = None
    for line in reversed(lines):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

    discovered_exports = sorted(str(p) for p in export_dir.glob("*") if p.is_file())

    if payload is None:
        return ExecutionResult(
            success=exit_code == 0,
            stdout=logs,
            error_traceback=None if exit_code == 0 else (logs or "Sandbox lieferte keine JSON-Ausgabe."),
            export_paths=discovered_exports,
        )

    success = payload.get("status") == "SUCCESS"
    export_paths = payload.get("export_paths") or discovered_exports

    return ExecutionResult(
        success=success,
        stdout=payload.get("stdout", ""),
        error_traceback=payload.get("traceback") or (
            f"{payload.get('error_type')}: {payload.get('error_message')}" if not success else None
        ),
        export_paths=export_paths,
    )
