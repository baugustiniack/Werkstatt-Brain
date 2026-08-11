import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints.cad import router as cad_router
from app.api.endpoints.conversations import router as conversations_router
from app.api.endpoints.inventory import router as inventory_router
from app.api.endpoints.meta_coach import router as meta_coach_router
from app.api.endpoints.agent_workflows import router as agent_workflows_router
from app.api.endpoints.settings import router as settings_router
from app.config import settings
from app.db.postgres import init_db, verify_postgres_connection
from app.db.qdrant import verify_qdrant_connection
from app.services import meta_coach
from app.services.sandbox_runner import is_docker_sandbox_available

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Werkstatt-Brain API (%s)", settings.app_env)
    verify_postgres_connection()
    init_db()
    verify_qdrant_connection()
    logger.info("All database connections ready")

    if is_docker_sandbox_available():
        logger.info("Docker Sandbox Execution Service verfügbar (image=%s)", settings.sandbox_docker_image)
    else:
        logger.warning(
            "Docker Sandbox Execution Service NICHT verfügbar – "
            "Fallback auf Subprozess-Sandbox (reduzierte Isolation, kein --network none)."
        )

    if settings.meta_coach_scheduler_enabled:
        meta_coach.start_scheduler()
    else:
        logger.info(
            "Meta-Coach-Scheduler deaktiviert (META_COACH_SCHEDULER_ENABLED=false) – "
            "manueller Lauf via app.services.meta_coach.run_nightly_cycle() weiterhin möglich."
        )

    yield

    if settings.meta_coach_scheduler_enabled:
        meta_coach.stop_scheduler()
    logger.info("Shutting down Werkstatt-Brain API")


app = FastAPI(
    title="Werkstatt-Brain API",
    description="Autonomes Werkstatt-Brain & CAD/CAM-Agenten-System",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    # Handy-Upload über LAN-IP (QR) – Origin weicht von localhost ab
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|(\d{1,3}\.){3}\d{1,3})(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cad_router)
app.include_router(inventory_router)
app.include_router(settings_router)
app.include_router(conversations_router)
app.include_router(meta_coach_router)
app.include_router(agent_workflows_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "environment": settings.app_env,
    }


@app.get("/api/v1/network/lan-hint")
async def lan_hint() -> dict[str, object]:
    """Hilft dem QR-Handy-Upload: bekannte LAN-IPs (ENV + Host-Raten).

    In Docker oft unvollständig – der Browser ergänzt per WebRTC. `LAN_HINT_IPS`
    im .env (kommagetrennt) überschreibt/ergänzt, z.B. `192.168.178.46`.
    """
    import os
    import socket

    ips: list[str] = []
    for part in (os.environ.get("LAN_HINT_IPS") or "").split(","):
        ip = part.strip()
        if ip and ip not in ips:
            ips.append(ip)

    for host in ("host.docker.internal", socket.gethostname()):
        try:
            info = socket.getaddrinfo(host, None, socket.AF_INET)
            for entry in info:
                ip = entry[4][0]
                if ip and not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except OSError:
            pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass

    return {
        "ips": ips,
        "frontend_port": int(os.environ.get("FRONTEND_PORT") or "5173"),
        "api_port": int(os.environ.get("API_PORT") or "8000"),
        "hint": (
            "Wenn die Liste leer/falsch ist: LAN_HINT_IPS=deine.wlan.ip in .env setzen "
            "oder im QR-Dialog die IP manuell wählen."
        ),
    }
