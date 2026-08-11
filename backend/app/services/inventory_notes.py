"""Hilfen für getrennte User-/KI-Beschreibungen in der Inventar-DB."""

from __future__ import annotations

from app.models.unprocessed_asset import UnprocessedAsset


def notes_blob(asset: UnprocessedAsset) -> str:
    """Alle Textfelder zusammen (Suche, Heuristiken, Force-Tags)."""
    parts = [asset.user_notes or "", asset.ai_notes or "", asset.notes or ""]
    return "\n".join(p for p in parts if p and p.strip())


def effective_ai_notes(asset: UnprocessedAsset) -> str | None:
    text = (asset.ai_notes or asset.notes or "").strip()
    return text or None


def effective_user_notes(asset: UnprocessedAsset) -> str | None:
    text = (asset.user_notes or "").strip()
    return text or None


def set_ai_notes(asset: UnprocessedAsset, text: str | None) -> None:
    """Schreibt KI-Text und spiegelt ihn nach `notes` (Legacy/Suche)."""
    cleaned = (text or "").strip() or None
    asset.ai_notes = cleaned
    asset.notes = cleaned


def set_user_notes(asset: UnprocessedAsset, text: str | None) -> None:
    asset.user_notes = (text or "").strip() or None


def combined_description(asset: UnprocessedAsset, *, vision_fallback: str | None = None) -> str:
    """Beschreibung für Inventory Manager / Knowledge Graph (User + KI)."""
    user = effective_user_notes(asset)
    ai = effective_ai_notes(asset)
    parts: list[str] = []
    if user:
        parts.append(f"Nutzer-Beschreibung:\n{user}")
    if ai:
        parts.append(f"KI-Beschreibung:\n{ai}")
    if parts:
        return "\n\n".join(parts)
    if vision_fallback and vision_fallback.strip():
        return vision_fallback.strip()
    return ""
