"""Persistenter Key-Value-Store für Laufzeit-Einstellungen (`app_settings`),
insbesondere API-Keys, die der Nutzer über die UI hinterlegt statt nur über
`.env` (Nutzer-Feedback zum Chapter-3-Frontend). Ein DB-Eintrag überschreibt
den entsprechenden `.env`-Wert; ist keiner gesetzt, wird auf `.env`
zurückgefallen, damit das System auch ohne UI-Interaktion lauffähig bleibt.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.db.postgres import SessionLocal
from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY_SETTING = "anthropic_api_key"
OPENAI_API_KEY_SETTING = "openai_api_key"
CURSOR_API_KEY_SETTING = "cursor_api_key"
LLM_PROVIDER_SETTING = "llm_provider"  # "auto" | "anthropic" | "cursor"


def get_setting(key: str) -> str | None:
    db = SessionLocal()
    try:
        row = db.get(AppSetting, key)
        return row.value if row else None
    except Exception as exc:  # noqa: BLE001 - Settings-Lookup darf den Aufrufer nie hart abbrechen
        logger.warning("Konnte Setting '%s' nicht lesen: %s", key, exc)
        return None
    finally:
        db.close()


def set_setting(key: str, value: str | None) -> None:
    db = SessionLocal()
    try:
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
            db.add(row)
        else:
            row.value = value
        db.commit()
    finally:
        db.close()


def resolve_anthropic_api_key() -> str:
    """DB-Override (UI-Eingabefeld) hat Vorrang vor `ANTHROPIC_API_KEY` aus `.env`."""
    return get_setting(ANTHROPIC_API_KEY_SETTING) or settings.anthropic_api_key


def resolve_openai_api_key() -> str:
    return get_setting(OPENAI_API_KEY_SETTING) or settings.openai_api_key


def resolve_cursor_api_key() -> str:
    """DB-Override hat Vorrang vor `CURSOR_API_KEY` aus `.env`."""
    return get_setting(CURSOR_API_KEY_SETTING) or settings.cursor_api_key


def is_anthropic_configured() -> bool:
    return bool(resolve_anthropic_api_key())


def is_cursor_configured() -> bool:
    return bool(resolve_cursor_api_key())


def is_llm_configured() -> bool:
    """True, wenn mindestens ein Text-LLM-Provider (Anthropic oder Cursor) hinterlegt ist."""
    return is_anthropic_configured() or is_cursor_configured()


def resolve_llm_provider() -> str:
    """Effektiver Provider: UI-Wahl (`auto`/`anthropic`/`cursor`), sonst Auto-Auswahl.

    Auto: Anthropic zuerst (schneller Direkt-API-Call + Vision), sonst Cursor.
    Explizite Wahl wird nur genutzt, wenn der gewählte Key auch wirklich gesetzt ist;
    sonst Fallback auf den anderen konfigurierten Provider.
    """
    preferred = (get_setting(LLM_PROVIDER_SETTING) or "auto").strip().lower()
    if preferred not in ("auto", "anthropic", "cursor"):
        preferred = "auto"

    anthropic_ok = is_anthropic_configured()
    cursor_ok = is_cursor_configured()

    if preferred == "anthropic" and anthropic_ok:
        return "anthropic"
    if preferred == "cursor" and cursor_ok:
        return "cursor"
    if anthropic_ok:
        return "anthropic"
    if cursor_ok:
        return "cursor"
    return "none"
