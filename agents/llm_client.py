"""Dünner LLM-Client-Wrapper für Concept Builder & 3D Builder (Erweiterungspunkt
aus SPEC Kap. 3.3/3.4, ursprünglich als Regex-/Template-Platzhalter markiert).

Unterstützt zwei optionale Provider (Nutzer-Feedback: Anthropic- und Cursor-
API-Key parallel hinterlegbar):
- Anthropic: direkter Claude-API-Call (schnell, auch Vision)
- Cursor: `cursor-sdk` Agent.prompt; Referenzfotos als Dateien im Temp-CWD
- OpenAI: Konzeptbild-Generierung (+ optionaler Vision-Fallback)

Aufrufer MÜSSEN selbst einen Heuristik-/Template-Fallback bereitstellen, falls
`is_llm_configured()` False liefert oder ein Call fehlschlägt – der Graph darf
nie hart von einem Cloud-API-Aufruf abhängen (SPEC Kap. 1.5)."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path
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


def _call_anthropic_text(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    *,
    images: list[tuple[bytes, str]] | None = None,
) -> str:
    import base64

    import anthropic

    api_key = settings_store.resolve_anthropic_api_key()
    if not api_key:
        raise RuntimeError("Kein Anthropic-API-Key konfiguriert (weder UI-Setting noch .env).")

    client = anthropic.Anthropic(api_key=api_key)
    content: list[dict[str, Any]] = []
    for raw, media_type in (images or [])[:4]:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type or "image/jpeg",
                    "data": base64.b64encode(raw).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": user_prompt})
    message = client.messages.create(
        model=settings.agent_llm_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(block.text for block in message.content if hasattr(block, "text"))


def _call_openai_vision_text(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    *,
    images: list[tuple[bytes, str]] | None = None,
) -> str:
    """Fallback wenn Anthropic fehlt, aber OpenAI-Key da ist (Chat-Referenzfotos)."""
    import base64

    from openai import OpenAI

    api_key = settings_store.resolve_openai_api_key()
    if not api_key:
        raise RuntimeError("Kein OpenAI-API-Key für Vision-Fallback.")

    client = OpenAI(api_key=api_key)
    parts: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    for raw, media_type in (images or [])[:4]:
        b64 = base64.b64encode(raw).decode("ascii")
        mime = media_type or "image/jpeg"
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "low"},
            }
        )
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": parts},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _call_cursor_text(
    system_prompt: str,
    user_prompt: str,
    *,
    images: list[tuple[bytes, str]] | None = None,
) -> str:
    """One-shot Cursor-Agent-Call. Bilder → Dateien im Temp-CWD (Cursor sieht Workspace)."""
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    api_key = settings_store.resolve_cursor_api_key()
    if not api_key:
        raise RuntimeError("Kein Cursor-API-Key konfiguriert (weder UI-Setting noch .env).")

    image_note = ""
    with tempfile.TemporaryDirectory(prefix="werkstatt-llm-") as tmp:
        tmp_path = Path(tmp)
        if images:
            names: list[str] = []
            for i, (raw, media_type) in enumerate(images[:4], start=1):
                ext = "jpg"
                mt = (media_type or "").lower()
                if "png" in mt:
                    ext = "png"
                elif "webp" in mt:
                    ext = "webp"
                name = f"referenz_{i:02d}.{ext}"
                (tmp_path / name).write_bytes(raw)
                names.append(name)
            index = tmp_path / "REFERENZ_FOTOS.md"
            index.write_text(
                "# Nutzer-Referenzfotos\n\n"
                "Diese Dateien liegen im aktuellen Workspace. "
                "Bitte die Bilder öffnen/betrachten und inhaltlich auswerten "
                "(Raum, Grundriss, Bemaßung, vorhandene Möbel).\n\n"
                + "\n".join(f"- `{n}`" for n in names)
                + "\n",
                encoding="utf-8",
            )
            image_note = (
                f"\n\nIm Workspace liegen {len(names)} Referenzfoto(s): "
                + ", ".join(names)
                + " sowie REFERENZ_FOTOS.md. "
                "ÖFFNE und ANALYSIERE diese Bilder, bevor du antwortest. "
                "Maße/Objekte aus den Fotos sind verbindlich.\n"
            )
            logger.info("Cursor-Agent: %s Referenzbild(er) als Dateien im Temp-CWD", len(names))

        combined = (
            f"{system_prompt}\n\n---\n\n{user_prompt}{image_note}\n\n"
            "WICHTIG: Antworte ausschließlich mit dem angeforderten Text bzw. JSON. "
            "Erstelle, ändere oder lösche keine weiteren Dateien und führe keine Shell-Befehle aus "
            "(außer dem Lesen/Betrachten der Referenzfotos)."
        )

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


def call_llm_text(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
    *,
    images: list[tuple[bytes, str]] | None = None,
) -> str:
    """Roher Text-Call; optional Bilder.

    Bei Cursor-Provider: Fotos als Dateien im Agent-Workspace (kein Anthropic nötig).
    Fallback: OpenAI Vision. Anthropic nur wenn Key vorhanden.
    """
    provider = settings_store.resolve_llm_provider()
    has_images = bool(images)

    if has_images:
        # 1) Cursor (gewählter Provider oder Auto mit Cursor-Key ohne Anthropic)
        if provider == "cursor" or (
            provider == "auto"
            and settings_store.resolve_cursor_api_key()
            and not settings_store.resolve_anthropic_api_key()
        ):
            if settings_store.resolve_cursor_api_key():
                try:
                    logger.info("Vision-LLM: Cursor mit %s Bilddatei(en) im Workspace", len(images))
                    return _call_cursor_text(system_prompt, user_prompt, images=images)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Cursor-Vision (Workspace-Dateien) fehlgeschlagen: %s", exc)
        # 2) Anthropic Vision (optional)
        if settings_store.resolve_anthropic_api_key():
            try:
                logger.info("Vision-LLM: Anthropic mit %s Bild(ern)", len(images))
                return _call_anthropic_text(system_prompt, user_prompt, max_tokens, images=images)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Anthropic-Vision fehlgeschlagen: %s", exc)
        # 3) OpenAI Vision Fallback
        if settings_store.resolve_openai_api_key():
            try:
                logger.info("Vision-LLM: OpenAI-Fallback mit %s Bild(ern)", len(images))
                return _call_openai_vision_text(system_prompt, user_prompt, max_tokens, images=images)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OpenAI-Vision fehlgeschlagen: %s", exc)
        logger.warning("Referenzbilder vorhanden, aber kein Vision-Pfad erfolgreich (%s)", provider)

    text_prompt = user_prompt
    if has_images:
        text_prompt = (
            f"{user_prompt}\n\n"
            f"[Hinweis: {len(images)} Referenzbild(er) waren angehängt, "
            "konnten nicht ausgewertet werden. Nutze id=-Zeilen im Prompt.]"
        )
    if provider == "anthropic":
        return _call_anthropic_text(system_prompt, text_prompt, max_tokens)
    if provider == "cursor":
        return _call_cursor_text(system_prompt, text_prompt)
    if settings_store.resolve_anthropic_api_key():
        return _call_anthropic_text(system_prompt, text_prompt, max_tokens)
    if settings_store.resolve_cursor_api_key():
        return _call_cursor_text(system_prompt, text_prompt)
    raise RuntimeError(
        "Kein LLM-Provider konfiguriert (weder Anthropic- noch Cursor-API-Key "
        "in UI-Settings oder .env)."
    )


def call_vision_prefer_anthropic(
    system_prompt: str,
    user_prompt: str,
    images: list[tuple[bytes, str]],
    *,
    max_tokens: int = 2048,
) -> str:
    """Referenzfoto-Analyse: Cursor (Workspace) zuerst wenn Provider=Cursor, sonst Anthropic/OpenAI.

    Name historisch; ohne Anthropic-Key läuft der Cursor-Pfad.
    """
    if not images:
        return call_llm_text(system_prompt, user_prompt, max_tokens=max_tokens)

    provider = settings_store.resolve_llm_provider()
    # Cursor-Modelle: Bilder als Dateien im Agent-CWD
    if settings_store.resolve_cursor_api_key() and provider in ("cursor", "auto"):
        try:
            logger.info("Referenz-Vision: Cursor-Workspace mit %s Bild(ern)", len(images))
            return _call_cursor_text(system_prompt, user_prompt, images=images)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cursor-Referenz-Vision fehlgeschlagen: %s", exc)

    if settings_store.resolve_anthropic_api_key():
        try:
            logger.info("Referenz-Vision: Anthropic (%s Bild(er))", len(images))
            return _call_anthropic_text(system_prompt, user_prompt, max_tokens, images=images)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Anthropic-Referenz-Vision fehlgeschlagen: %s", exc)

    if settings_store.resolve_openai_api_key():
        logger.info("Referenz-Vision: OpenAI-Fallback (%s Bild(er))", len(images))
        return _call_openai_vision_text(system_prompt, user_prompt, max_tokens, images=images)

    raise RuntimeError("Kein Vision-fähiger Pfad (Cursor/Anthropic/OpenAI) für Referenzfotos.")


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
    *,
    images: list[tuple[bytes, str]] | None = None,
) -> dict[str, Any]:
    """Wie `call_llm_text`, extrahiert aber das erste JSON-Objekt aus der Antwort."""
    text = call_llm_text(system_prompt, user_prompt, max_tokens=max_tokens, images=images)
    return _extract_json_object(text)
