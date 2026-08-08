import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints.cad import router as cad_router
from app.api.endpoints.conversations import router as conversations_router
from app.api.endpoints.inventory import router as inventory_router
from app.api.endpoints.meta_coach import router as meta_coach_router
from app.api.endpoints.settings import router as settings_router
from app.config import settings
from app.db.postgres import verify_postgres_connection
from app.db.qdrant import verify_qdrant_connection
from app.services import meta_coach
from app.services.sandbox_runner import is_docker_sandbox_available

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Werkstatt-Brain API (%s)", settings.app_env)
    verify_postgres_connection()
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cad_router)
app.include_router(inventory_router)
app.include_router(settings_router)
app.include_router(conversations_router)
app.include_router(meta_coach_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "environment": settings.app_env,
    }
