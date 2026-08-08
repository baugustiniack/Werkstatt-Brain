"""Einstellungs-Endpunkte für UI-verwaltete API-Keys (Nutzer-Feedback: 'API
Key flexibel als Eingabefeld im UI einfügen' statt nur `.env`).

Der Klartext-Key wird niemals wieder ausgegeben – nur `*_configured`-Statusflags
und der gewählte Provider, damit das Frontend anzeigen kann, ob LLM-gestützte
Agenten-Schritte (Concept Builder, 3D Builder, Vision-Ingestion) aktiv sind.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import settings_store

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

ProviderChoice = Literal["auto", "anthropic", "cursor"]


class ApiKeyStatusResponse(BaseModel):
    anthropic_configured: bool
    openai_configured: bool
    cursor_configured: bool
    llm_provider: ProviderChoice
    active_provider: str  # effektiver Provider nach Auto-Auflösung: anthropic|cursor|none


class ApiKeyUpdateRequest(BaseModel):
    anthropic_api_key: str | None = Field(None, description="Leerstring/None löscht den hinterlegten Key wieder.")
    openai_api_key: str | None = Field(None, description="Leerstring/None löscht den hinterlegten Key wieder.")
    cursor_api_key: str | None = Field(None, description="Leerstring/None löscht den hinterlegten Key wieder.")
    llm_provider: ProviderChoice | None = Field(
        None, description="Bevorzugter Provider: auto | anthropic | cursor"
    )


def _status_response() -> ApiKeyStatusResponse:
    preferred = (settings_store.get_setting(settings_store.LLM_PROVIDER_SETTING) or "auto").strip().lower()
    if preferred not in ("auto", "anthropic", "cursor"):
        preferred = "auto"
    return ApiKeyStatusResponse(
        anthropic_configured=settings_store.is_anthropic_configured(),
        openai_configured=bool(settings_store.resolve_openai_api_key()),
        cursor_configured=settings_store.is_cursor_configured(),
        llm_provider=preferred,  # type: ignore[arg-type]
        active_provider=settings_store.resolve_llm_provider(),
    )


@router.get("/api-keys", response_model=ApiKeyStatusResponse)
def get_api_key_status() -> ApiKeyStatusResponse:
    return _status_response()


@router.post("/api-keys", response_model=ApiKeyStatusResponse)
def update_api_keys(request: ApiKeyUpdateRequest) -> ApiKeyStatusResponse:
    if request.anthropic_api_key is not None:
        settings_store.set_setting(settings_store.ANTHROPIC_API_KEY_SETTING, request.anthropic_api_key.strip() or None)
    if request.openai_api_key is not None:
        settings_store.set_setting(settings_store.OPENAI_API_KEY_SETTING, request.openai_api_key.strip() or None)
    if request.cursor_api_key is not None:
        settings_store.set_setting(settings_store.CURSOR_API_KEY_SETTING, request.cursor_api_key.strip() or None)
    if request.llm_provider is not None:
        settings_store.set_setting(settings_store.LLM_PROVIDER_SETTING, request.llm_provider)

    return _status_response()
