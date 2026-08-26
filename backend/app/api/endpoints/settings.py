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
ConceptRosterMode = Literal["auto", "manual"]


class ApiKeyStatusResponse(BaseModel):
    anthropic_configured: bool
    openai_configured: bool
    cursor_configured: bool
    llm_provider: ProviderChoice
    active_provider: str  # effektiver Provider: cursor|anthropic|none
    vision_configured: bool
    concept_roster_mode: ConceptRosterMode
    concept_roster_agents: list[str]
    concept_roster_selectable: list[str]


class ApiKeyUpdateRequest(BaseModel):
    anthropic_api_key: str | None = Field(None, description="Leerstring/None löscht den hinterlegten Key wieder.")
    openai_api_key: str | None = Field(None, description="Leerstring/None löscht den hinterlegten Key wieder.")
    cursor_api_key: str | None = Field(None, description="Leerstring/None löscht den hinterlegten Key wieder.")
    llm_provider: ProviderChoice | None = Field(
        None, description="Bevorzugter Provider: auto | anthropic | cursor"
    )
    concept_roster_mode: ConceptRosterMode | None = Field(
        None, description="Konzept-Roster: auto (Supervisor) | manual (Nutzer-Auswahl)"
    )
    concept_roster_agents: list[str] | None = Field(
        None, description="Agent-IDs für manuelles Konzept-Roster"
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
        vision_configured=settings_store.is_vision_configured(),
        concept_roster_mode=settings_store.resolve_concept_roster_mode(),  # type: ignore[arg-type]
        concept_roster_agents=settings_store.resolve_manual_concept_roster(),
        concept_roster_selectable=list(settings_store.CONCEPT_ROSTER_SELECTABLE),
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
    if request.concept_roster_mode is not None:
        settings_store.set_setting(settings_store.CONCEPT_ROSTER_MODE_SETTING, request.concept_roster_mode)
    if request.concept_roster_agents is not None:
        import json

        allowed = set(settings_store.CONCEPT_ROSTER_SELECTABLE)
        picked = [aid for aid in request.concept_roster_agents if aid in allowed]
        settings_store.set_setting(
            settings_store.CONCEPT_ROSTER_AGENTS_SETTING,
            json.dumps(picked) if picked else None,
        )

    return _status_response()
