"""Dünner LLM-Client-Wrapper für Concept Builder & 3D Builder (Erweiterungspunkt
aus SPEC Kap. 3.3/3.4, ursprünglich als Regex-/Template-Platzhalter markiert).

Unterstützt zwei optionale Provider (Nutzer-Feedback: Anthropic- und Cursor-
API-Key parallel hinterlegbar):
- Anthropic: direkter Claude-API-Call (schnell, auch Vision)
- Cursor: `cursor-sdk` Agent.prompt (Composer), Text-only in einem Temp-CWD

Aufrufer MÜSSEN selbst einen Heuristik-/Template-Fallback bereitstellen, falls
`is_llm_configured()` False liefert oder ein Call fehlschlägt – der Graph darf
nie hart von einem Cloud-API-Aufruf abhängen (SPEC Kap. 1.5)."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from typing import Any

from app.config import settings
from app.services import settings_store

logger = logging.getLogger(__name__)

_JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_CODE_BLOCK_PATTERN = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def is_llm_configured() -> bool:
    return settings_store.is_llm_configured()


def _extract_json_object(text: str) -> dict[str, Any]:
    match = _JSON_BLOCK_PATTERN.search(text)
    if not match:
        raise ValueError(f"Keine JSON-Struktur in LLM-Antwort gefunden: {text[:200]!r}")
    return json.loads(match.group(0))


def extract_code_block(text: str) -> str:
    """Extrahiert den Inhalt des ersten Markdown-Codeblocks; fällt auf den
    kompletten (getrimmten) Text zurück, falls die Antwort keinen enthält."""
    match = _CODE_BLOCK_PATTERN.search(text)
    return match.group(1).strip() if match else text.strip()


def _call_anthropic_text(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    import anthropic

    api_key = settings_store.resolve_anthropic_api_key()
    if not api_key:
        raise RuntimeError("Kein Anthropic-API-Key konfiguriert (weder UI-Setting noch .env).")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=settings.agent_llm_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in message.content if hasattr(block, "text"))


def _call_cursor_text(system_prompt: str, user_prompt: str) -> str:
    """One-shot Cursor-Agent-Call für reine Text-/JSON-Antworten.

    Läuft in einem leeren Temp-CWD, damit der Agent keine Projektdateien
    anfasst. Braucht den lokalen SDK-Bridge-Prozess (kommt mit `cursor-sdk`);
    schlägt der Call fehl, greifen die Aufrufer auf Heuristik zurück.
    """
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    api_key = settings_store.resolve_cursor_api_key()
    if not api_key:
        raise RuntimeError("Kein Cursor-API-Key konfiguriert (weder UI-Setting noch .env).")

    combined = (
        f"{system_prompt}\n\n---\n\n{user_prompt}\n\n"
        "WICHTIG: Antworte ausschließlich mit dem angeforderten Text bzw. JSON. "
        "Erstelle, ändere oder lösche keine Dateien und führe keine Shell-Befehle aus."
    )

    with tempfile.TemporaryDirectory(prefix="werkstatt-llm-") as tmp:
        result = Agent.prompt(
            combined,
            AgentOptions(
                api_key=api_key,
                model=settings.agent_cursor_model,
                local=LocalAgentOptions(cwd=tmp),
            ),
        )

    if getattr(result, "status", None) == "error":
        raise RuntimeError("Cursor-Agent-Lauf fehlgeschlagen (status=error).")

    text = (getattr(result, "result", None) or "").strip()
    if not text:
        raise RuntimeError("Cursor-Agent lieferte eine leere Antwort.")
    return text


def call_llm_text(system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """Roher Text-Call gegen den konfigurierten Provider (Anthropic oder Cursor).

    Wirft bei fehlendem Key/Fehler – Aufrufer müssen einen `try/except` mit
    Heuristik-Fallback um den Aufruf legen.
    """
    provider = settings_store.resolve_llm_provider()
    if provider == "anthropic":
        return _call_anthropic_text(system_prompt, user_prompt, max_tokens)
    if provider == "cursor":
        return _call_cursor_text(system_prompt, user_prompt)
    raise RuntimeError(
        "Kein LLM-Provider konfiguriert (weder Anthropic- noch Cursor-API-Key "
        "in UI-Settings oder .env)."
    )


def call_llm_json(system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> dict[str, Any]:
    """Wie `call_llm_text`, extrahiert aber das erste JSON-Objekt aus der Antwort."""
    text = call_llm_text(system_prompt, user_prompt, max_tokens=max_tokens)
    return _extract_json_object(text)
