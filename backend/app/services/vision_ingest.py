"""AI Vision Ingestion Pipeline – multimodale Klassifizierung von Fotos und
Dokumenten (SPEC Kap. 2.3.2).

Bilder (PNG/JPG/…): ausschließlich OpenAI Vision (`OPENAI_API_KEY` / UI-Setting) –
Nutzer-Feedback: Anthropic Vision funktioniert hier nicht zuverlässig.
Dokumente/CAD: Text-LLM (Anthropic oder Cursor) über `analyze_document_file`.

Ohne passenden Key greift eine dateinamen-basierte Heuristik als Fallback.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.qdrant import VISUAL_INVENTORY_COLLECTION, get_qdrant_client, random_placeholder_vector
from app.models.stock_material import StockMaterial
from app.models.tool import Tool, ToolStatus
from app.models.unprocessed_asset import UnprocessedAsset
from app.services import settings_store
from app.services.inventory_notes import (
    effective_ai_notes,
    effective_user_notes,
    notes_blob,
    set_ai_notes,
)

logger = logging.getLogger(__name__)

_JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)

_VISION_SYSTEM_PROMPT = """Du bist ein Experte für Werkstatt-Inventar (CNC-Fräser, Holz-/Materiallager, \
Produktkonzepte, Referenzfotos).
Analysiere das Bild und antworte AUSSCHLIESSLICH mit einem einzelnen JSON-Objekt (keine Erklärungen, \
kein Markdown) exakt in diesem Format:
{
  "category": "tool" | "material" | "product_concept" | "reference_photo" | "cad_reference" | "document" | "unknown",
  "tool_type": "flat_endmill" | "ball_nose" | "v_bit" | "drill" | null,
  "material_type": string | null,
  "estimated_dimensions": {"diameter_mm": number, "length_mm": number, "width_mm": number, \
"thickness_mm": number, "angle_deg": number},
  "description": string,
  "tags": [string, ...]
}

Kategorie-Regeln (streng einhalten):
- "tool": NUR echte CNC-/Handwerkzeuge (Fräser, Bohrer, Bits) – Nahaufnahmen von Werkzeugen.
- "material": NUR Rohmaterial / Lagerware (Plattenstapel, Reststücke, Kanthölzer, Bleche im Lager). \
Kein fertiges Möbelstück und keine Raumvisualisierung.
- "product_concept": Möbel-/Produktentwürfe, Raumfotos mit fertigem Schrank/Regal, KI-Renders, Design-Mockups, \
Marketing-/Konzeptbilder von Bauteilen oder Einrichtungen.
- "reference_photo": sonstige Referenzfotos (Personen, Werkstattumgebung, Skizzenfotos) ohne klare Tool-/Material-Zuordnung.
- "cad_reference": technische Zeichnungen, Screenshots aus CAD, Explosionsansichten.
- "document": gescannte Dokumente, Listen, Etiketten.
- "unknown": nur wenn nichts Sinnvolles passt.

WICHTIG:
- Ein hölzerner Schrank / Eckschrank / Möbelstück im Raum ist IMMER "product_concept", NIEMALS "material".
- "material" nur, wenn das Motiv klar Rohware/Lagerbestand ist (nicht das fertige Produkt).
- Das Feld "description" ist PFLICHT und darf NIE leer sein (mindestens 3–6 Sätze auf Deutsch).
- Wenn eine Nutzer-Beschreibung mitgeliefert wird: inhaltlich einbeziehen und ergänzen \
(nicht ignorieren, nicht wortgleich kopieren; Widersprüche klar benennen).
- Beschreibe sichtbar: Motiv, Bildtyp (Foto/Scan/Screenshot/KI-Render/Zeichnung), Farben, Materialien, Maße falls erkennbar, \
Werkstatt-Relevanz.
- Bei Personenfotos: KEINE Identifikation/Namen. Beschreibe nur Bildausschnitt, Umgebung, Kleidung/Kontext \
und warum das Foto im Inventar nützlich sein könnte (z.B. Referenzperson, Dokumentation).
- Lasse nicht anwendbare Felder in estimated_dimensions einfach weg.
- Tags: kurze deutsche oder englische Stichworte; bei KI-Renders/Konzepten "KI-Generiert" setzen, wenn erkennbar."""


AssetCategory = Literal[
    "tool",
    "material",
    "product_concept",
    "reference_photo",
    "cad_reference",
    "document",
    "unknown",
]

# Tags, die eine feste Kategorie erzwingen (kein Vision-Override zu tool/material)
_FORCED_CONCEPT_TAGS = frozenset({"concept_image", "product_concept"})
_FORCED_CAD_MODEL_TAGS = frozenset({"generated_3d", "cad_model"})
_PROTECTED_TAGS = frozenset(
    {"KI-Generiert", "concept_image", "from_chat", "generated_3d", "cad_model"}
)


class VisionIngestResult(BaseModel):
    """Strukturiertes Ergebnis der Vision-/Dokument-Analyse (SPEC Kap. 2.3.2)."""

    category: AssetCategory = "unknown"
    tool_type: str | None = None
    material_type: str | None = None
    estimated_dimensions: dict[str, float] = Field(default_factory=dict)
    description: str = ""
    tags: list[str] = Field(default_factory=list)

def _extract_json_object(text: str) -> dict[str, Any]:
    match = _JSON_BLOCK_PATTERN.search(text)
    if not match:
        raise ValueError(f"Keine JSON-Struktur in Vision-Antwort gefunden: {text[:200]!r}")
    return json.loads(match.group(0))


def _call_anthropic_vision(image_bytes: bytes, media_type: str) -> dict[str, Any]:
    """Legacy – Bildanalyse läuft über OpenAI; Funktion bleibt für Tests/Notfälle."""
    import base64

    import anthropic

    client = anthropic.Anthropic(api_key=settings_store.resolve_anthropic_api_key())
    encoded = base64.b64encode(image_bytes).decode("utf-8")

    message = client.messages.create(
        model=settings.vision_model,
        max_tokens=1024,
        system=_VISION_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": encoded},
                    },
                    {"type": "text", "text": "Analysiere dieses Werkstatt-Bild und liefere das JSON."},
                ],
            }
        ],
    )
    text = "".join(block.text for block in message.content if hasattr(block, "text"))
    return _extract_json_object(text)


def _call_openai_vision(
    image_bytes: bytes,
    media_type: str,
    *,
    hint: str | None = None,
    user_notes: str | None = None,
) -> dict[str, Any]:
    import base64

    from openai import OpenAI

    api_key = settings_store.resolve_openai_api_key()
    if not api_key:
        raise RuntimeError("Kein OpenAI-API-Key konfiguriert.")

    client = OpenAI(api_key=api_key)
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{media_type};base64,{encoded}"
    model = settings.openai_vision_model or "gpt-4o-mini"
    hint_text = f"\nZusatzhinweis zur Klassifizierung: {hint}" if hint else ""
    user_text = ""
    if user_notes and user_notes.strip():
        user_text = (
            f"\n\nNutzer-Beschreibung (MUSS in die KI-Beschreibung einbezogen werden):\n"
            f"{user_notes.strip()}"
        )

    response = client.chat.completions.create(
        model=model,
        max_tokens=1500,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Analysiere dieses Inventar-Bild und liefere das JSON. "
                            "description muss ausführlich und nicht leer sein."
                            f"{hint_text}{user_text}"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("OpenAI Vision lieferte eine leere Antwort.")
    raw = _extract_json_object(text)
    description = str(raw.get("description") or "").strip()
    if not description:
        # Manche Antworten lassen description weg (z.B. bei Personenfotos) –
        # dann Rohtext bzw. Pflicht-Fallback erzwingen.
        raise RuntimeError(
            "OpenAI Vision lieferte JSON ohne description "
            f"(keys={sorted(raw.keys())})."
        )
    raw["description"] = description
    if not isinstance(raw.get("tags"), list):
        raw["tags"] = []
    raw["category"] = _normalize_category(raw.get("category"))
    return raw


def _normalize_category(value: Any) -> AssetCategory:
    """Mappt Modell-Ausgaben auf erlaubte Kategorien; sinnvolle Fallbacks."""
    raw = str(value or "unknown").strip().lower().replace(" ", "_").replace("-", "_")
    aliases: dict[str, AssetCategory] = {
        "tool": "tool",
        "material": "material",
        "product_concept": "product_concept",
        "product": "product_concept",
        "concept": "product_concept",
        "furniture": "product_concept",
        "moebel": "product_concept",
        "möbel": "product_concept",
        "design": "product_concept",
        "render": "product_concept",
        "ki_generiert": "product_concept",
        "reference_photo": "reference_photo",
        "reference": "reference_photo",
        "photo": "reference_photo",
        "cad_reference": "cad_reference",
        "cad": "cad_reference",
        "document": "document",
        "doc": "document",
        "unknown": "unknown",
    }
    return aliases.get(raw, "unknown")


def _asset_forces_cad_model(asset: UnprocessedAsset) -> bool:
    """KI-generierte STEP/STL aus dem Chat → immer cad_reference."""
    from app.models.unprocessed_asset import AssetFileType

    tags = {str(t) for t in (asset.tags or [])}
    if tags & _FORCED_CAD_MODEL_TAGS:
        return True
    if asset.file_type in (AssetFileType.STEP, AssetFileType.STL, AssetFileType.F3D) and (
        "KI-Generiert" in tags or "from_chat" in tags
    ):
        return True
    notes = notes_blob(asset).lower()
    if "ki-generiertes 3d-modell" in notes or "generated_3d" in notes:
        return True
    return False


def _asset_forces_product_concept(asset: UnprocessedAsset) -> bool:
    if _asset_forces_cad_model(asset):
        return False
    tags = {str(t) for t in (asset.tags or [])}
    if tags & _FORCED_CONCEPT_TAGS:
        return True
    if "KI-Generiert" in tags and "concept_image" in tags:
        return True
    # Dateiname aus Konzept-Pipeline
    path = (asset.file_path or "").lower()
    title = (asset.title or "").lower()
    if "_concept_" in path or path.endswith("_concept.png") or "konzept" in title:
        return True
    notes = notes_blob(asset).lower()
    if "konzept-foto aus cad-session" in notes:
        return True
    return False


def _apply_result_guards(asset: UnprocessedAsset, result: VisionIngestResult) -> VisionIngestResult:
    """Korrigiert sinnlose Klassifizierungen (z.B. Möbel-Konzept als material)."""
    if _asset_forces_cad_model(asset):
        result.category = "cad_reference"
        result.tool_type = None
        result.tags = list(
            dict.fromkeys([*(result.tags or []), "KI-Generiert", "generated_3d", "cad_model", "cad_reference"])
        )
        return result

    if _asset_forces_product_concept(asset):
        result.category = "product_concept"
        result.tool_type = None
        # material_type darf als Beschreibungshinweis bleiben, aber Kategorie nicht
        merged = list(dict.fromkeys([*(result.tags or []), "KI-Generiert", "concept_image", "product_concept"]))
        result.tags = merged
        return result

    # Fertige Möbelmotive dürfen nicht als Lager-Material landen
    desc = (result.description or "").lower()
    furniture_hints = (
        "schrank",
        "eckschrank",
        "regal",
        "möbel",
        "moebel",
        "kommode",
        "sideboard",
        "raumfoto",
        "wohnraum",
        "konzept",
        "render",
        "ki-generiert",
        "visualisierung",
    )
    if result.category == "material" and any(h in desc for h in furniture_hints):
        result.category = "product_concept"
        result.tags = list(dict.fromkeys([*(result.tags or []), "product_concept"]))
    return result


def _merge_notes(existing: str | None, description: str) -> str:
    """Beschreibung setzen, aber Chat-/Session-Verknüpfungen aus alten Notizen behalten."""
    desc = description.strip()
    if not existing or not existing.strip():
        return desc
    keep_lines: list[str] = []
    for line in existing.splitlines():
        low = line.strip().lower()
        if (
            low.startswith("verknüpfte unterhaltung:")
            or low.startswith("konzept-foto aus cad-session")
            or low.startswith("ki-generiertes 3d-modell")
            or low.startswith("session:")
            or low.startswith("teil-index:")
            or low.startswith("projekt:")
        ):
            keep_lines.append(line.strip())
        elif "inventar-db übernommen" in low:
            keep_lines.append(line.strip())
    if not keep_lines:
        return desc
    return f"{desc}\n\n" + "\n".join(dict.fromkeys(keep_lines))


def _preserve_protected_tags(existing: list[str] | None, incoming: list[str] | None) -> list[str]:
    kept_protected = [
        t
        for t in (existing or [])
        if t in _PROTECTED_TAGS
        or str(t).startswith("conversation:")
        or str(t).startswith("part:")
    ]
    other_existing = [
        t
        for t in (existing or [])
        if t not in _PROTECTED_TAGS
        and not str(t).startswith("conversation:")
        and not str(t).startswith("part:")
        and t not in {"heuristic_fallback", "cad_raw", "vision_error"}
    ]
    return list(dict.fromkeys([*kept_protected, *other_existing, *(incoming or [])]))


def _heuristic_fallback(file_path: str) -> dict[str, Any]:
    """Dateinamen-basierte Heuristik, solange kein OpenAI-Vision-Key konfiguriert ist."""
    stem = Path(file_path).stem.lower()
    name = Path(file_path).name.lower()

    tool_keywords = ("fraeser", "fräser", "bit", "endmill", "bohrer", "nutfraeser", "v_bit", "vbit")
    material_keywords = ("holz", "platte", "multiplex", "mdf", "eiche", "birke", "aluminium", "restholz")
    concept_keywords = ("concept", "konzept", "render", "mockup")

    if any(keyword in name or keyword in stem for keyword in concept_keywords):
        category, tool_type, material_type = "product_concept", None, None
        tags = ["heuristic_fallback", "KI-Generiert", "concept_image"]
    elif any(keyword in stem for keyword in tool_keywords):
        category, tool_type, material_type = "tool", "unknown", None
        tags = ["heuristic_fallback"]
    elif any(keyword in stem for keyword in material_keywords):
        category, tool_type, material_type = "material", None, "unbekannt"
        tags = ["heuristic_fallback"]
    else:
        category, tool_type, material_type = "unknown", None, None
        tags = ["heuristic_fallback"]

    return {
        "category": category,
        "tool_type": tool_type,
        "material_type": material_type,
        "estimated_dimensions": {},
        "description": (
            f"Automatische Heuristik (kein OpenAI-API-Key für Bildanalyse) für Datei "
            f"'{Path(file_path).name}'. Bitte unter Einstellungen einen OpenAI-Key hinterlegen "
            f"und „KI beschreiben“ erneut ausführen."
        ),
        "tags": tags,
    }

def is_vision_configured() -> bool:
    """Bildanalyse erfordert einen OpenAI-API-Key (Nutzer-Feedback)."""
    return bool(settings_store.resolve_openai_api_key())


def analyze_asset_image(
    file_path: str,
    *,
    classification_hint: str | None = None,
    user_notes: str | None = None,
) -> VisionIngestResult:
    """Analysiert ein Bild ausschließlich per OpenAI Vision."""
    path = Path(file_path)
    media_type = mimetypes.guess_type(path.name)[0] or "image/png"
    image_bytes = path.read_bytes()

    if not settings_store.resolve_openai_api_key():
        return VisionIngestResult(**_heuristic_fallback(file_path))

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            raw_result = _call_openai_vision(
                image_bytes,
                media_type,
                hint=classification_hint,
                user_notes=user_notes,
            )
            return VisionIngestResult(**raw_result)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "OpenAI-Vision-Analyse fehlgeschlagen für %s (Versuch %s): %s",
                file_path,
                attempt + 1,
                exc,
            )

    # Key ist da, aber Call/JSON unbrauchbar – nicht still den alten Stub behalten
    return VisionIngestResult(
        category="unknown",
        description=(
            f"OpenAI-Bildanalyse für '{path.name}' fehlgeschlagen "
            f"({last_error}). Bitte erneut „KI beschreiben“ versuchen oder die Beschreibung manuell ergänzen."
        ),
        tags=["vision_error"],
    )

def asset_needs_ai_rescan(asset: UnprocessedAsset) -> bool:
    """True für pending/failed oder Stub-/Heuristik-Beschreibungen ohne echte KI."""
    from app.models.unprocessed_asset import AssetStatus

    if asset.status in (AssetStatus.PENDING, AssetStatus.FAILED):
        return True
    tags = asset.tags or []
    if "heuristic_fallback" in tags or "cad_raw" in tags:
        return True
    notes = notes_blob(asset).lower()
    stubs = (
        "kein vision-modell konfiguriert",
        "rohe cad-datei ohne vision",
        "automatische heuristik",
        "bitte manuell prüfen",
        "bitte beschreibung manuell ergänzen",
    )
    if any(s in notes for s in stubs):
        return True
    vision = asset.vision_result if isinstance(asset.vision_result, dict) else {}
    desc = str(vision.get("description") or "").lower()
    if any(s in desc for s in stubs):
        return True
    return False


_MANUAL_ENTRY_SYSTEM_PROMPT = """Du bist ein Experte für Werkstatt-Inventar (CNC-Fräser, Holz-/Materiallager, Produktkonzepte).
Ein Nutzer hat einen manuellen Inventar-Eintrag ohne Datei angelegt (Titel + optionale Nutzer-Beschreibung). \
Analysiere Titel und Nutzer-Beschreibung und antworte AUSSCHLIESSLICH mit einem einzelnen JSON-Objekt (keine Erklärungen, \
kein Markdown) exakt in diesem Format:
{
  "category": "tool" | "material" | "product_concept" | "reference_photo" | "cad_reference" | "document" | "unknown",
  "tool_type": "flat_endmill" | "ball_nose" | "v_bit" | "drill" | null,
  "material_type": string | null,
  "estimated_dimensions": {"diameter_mm": number, "length_mm": number, "width_mm": number, \
"thickness_mm": number, "angle_deg": number},
  "description": string,
  "tags": [string, ...]
}
Fertige Möbel/Projekte → product_concept. Rohware → material. Werkzeuge → tool.
Die Nutzer-Beschreibung MUSS in "description" einbezogen und sinnvoll ergänzt werden (nicht ignorieren).
Lasse nicht anwendbare Felder in estimated_dimensions einfach weg."""


def _call_anthropic_text(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(api_key=settings_store.resolve_anthropic_api_key())
    message = client.messages.create(
        model=settings.vision_model,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(block.text for block in message.content if hasattr(block, "text"))
    return _extract_json_object(text)


def _heuristic_fallback_text(title: str, notes: str) -> dict[str, Any]:
    combined = f"{title} {notes}".lower()
    tool_keywords = ("fraeser", "fräser", "bit", "endmill", "bohrer", "nutfraeser", "v_bit", "vbit")
    material_keywords = ("holzplatte", "plattenstapel", "multiplex", "mdf-platte", "restholz", "lagerware")
    concept_keywords = ("schrank", "möbel", "moebel", "konzept", "regal", "eckschrank", "kommode")

    if any(keyword in combined for keyword in tool_keywords):
        category, tool_type, material_type = "tool", "unknown", None
    elif any(keyword in combined for keyword in concept_keywords):
        category, tool_type, material_type = "product_concept", None, None
    elif any(keyword in combined for keyword in material_keywords):
        category, tool_type, material_type = "material", None, "unbekannt"
    else:
        category, tool_type, material_type = "unknown", None, None

    tags = ["heuristic_fallback"]
    if category == "product_concept":
        tags.append("product_concept")

    return {
        "category": category,
        "tool_type": tool_type,
        "material_type": material_type,
        "estimated_dimensions": {},
        "description": f"Automatische Heuristik (kein Vision-Modell konfiguriert) für Eintrag '{title}'.",
        "tags": tags,
    }


def analyze_manual_entry(title: str, notes: str) -> VisionIngestResult:
    """Strukturiert einen manuellen Inventar-Eintrag (Titel + Nutzer-Beschreibung)
    per Text-LLM-Call. Die Nutzer-Beschreibung fließt in die KI-Beschreibung ein."""
    if settings_store.is_anthropic_configured():
        try:
            raw_result = _call_anthropic_text(
                _MANUAL_ENTRY_SYSTEM_PROMPT,
                f"Titel: {title}\nNutzer-Beschreibung: {notes or '(keine)'}",
            )
            raw_result["category"] = _normalize_category(raw_result.get("category"))
            return VisionIngestResult(**raw_result)
        except Exception as exc:  # noqa: BLE001 - LLM-Ausfall darf die Pipeline nicht stoppen
            logger.warning("Anthropic-Text-Analyse fehlgeschlagen für manuellen Eintrag '%s': %s", title, exc)

    return VisionIngestResult(**_heuristic_fallback_text(title, notes))


def _upsert_visual_inventory_payload(file_path: str, result: VisionIngestResult) -> None:
    try:
        client = get_qdrant_client()
        client.upsert(
            collection_name=VISUAL_INVENTORY_COLLECTION,
            points=[
                {
                    "id": str(uuid.uuid4()),
                    "vector": random_placeholder_vector(),
                    "payload": {
                        "asset_id": file_path,
                        "file_path": file_path,
                        "category": result.category,
                        "description": result.description,
                        "tags": result.tags,
                    },
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - Qdrant-Ausfall darf die Ingestion nicht stoppen
        logger.warning("Konnte visual_inventory-Payload nicht in Qdrant schreiben (%s): %s", file_path, exc)


def route_vision_result(
    db: Session,
    file_path: str,
    result: VisionIngestResult,
    *,
    allow_structured_routing: bool = True,
) -> dict[str, Any]:
    """Routet ein Vision-Ergebnis nach PostgreSQL (`tools`/`stock_materials`) und
    als Vektor-Payload nach Qdrant (`visual_inventory`) – SPEC Kap. 2.3.2.

    Strukturierte Tabellen nur bei klaren tool/material-Fällen – nie bei
    Produktkonzepten, Referenzfotos oder Dokumenten.
    """
    created_record: dict[str, str] | None = None
    dims = result.estimated_dimensions or {}

    if allow_structured_routing and result.category == "tool":
        tool = Tool(
            name=result.description[:255] or f"Erkanntes Werkzeug ({Path(file_path).name})",
            diameter_mm=dims.get("diameter_mm") or dims.get("shank_diameter_mm") or 0.0,
            status=ToolStatus.NEU,
        )
        db.add(tool)
        db.flush()
        created_record = {"table": "tools", "id": str(tool.id)}
    elif allow_structured_routing and result.category == "material":
        material = StockMaterial(
            material_type=result.material_type or "unbekannt",
            dimensions_xyz_mm={
                "x": dims.get("length_mm", 0),
                "y": dims.get("width_mm", 0),
                "z": dims.get("thickness_mm", 0),
            },
            notes=result.description,
        )
        db.add(material)
        db.flush()
        created_record = {"table": "stock_materials", "id": str(material.id)}

    _upsert_visual_inventory_payload(file_path, result)

    return {"created_record": created_record}

_DOCUMENT_SYSTEM_PROMPT = """Du bist ein Experte für Werkstatt-, CNC- und CAD-Dokumentation.
Dir wird der Inhalt oder die Metadaten einer hochgeladenen Datei gegeben (PDF, CAD, Text, Bild-Kontext, …).
Erstelle eine UMFASSENDE, für Menschen lesbare Beschreibung auf Deutsch, die später vom Inventory-Manager
und vom 3D-Builder genutzt wird, um Konzepte und Bauteile an vorhandene Referenzen anzupassen.

Antworte AUSSCHLIESSLICH mit einem einzelnen JSON-Objekt (kein Markdown) exakt in diesem Format:
{
  "category": "tool" | "material" | "product_concept" | "reference_photo" | "cad_reference" | "document" | "unknown",
  "tool_type": "flat_endmill" | "ball_nose" | "v_bit" | "drill" | null,
  "material_type": string | null,
  "estimated_dimensions": {"diameter_mm": number, "length_mm": number, "width_mm": number, \
"thickness_mm": number, "angle_deg": number},
  "description": string,
  "tags": [string, ...]
}

Kategorie-Hinweise:
- CAD/STEP/STL/F3D und technische Zeichnungen → "cad_reference"
- Text-/PDF-Anleitungen, Stücklisten → "document"
- Fertige Produktbeschreibungen / Möbelkonzepte → "product_concept"
- Rohmaterial-Lagerdaten → "material"
- Werkzeugdaten → "tool"

Anforderungen an "description":
- 2–6 Absätze bzw. strukturierte Aufzählung
- Was ist die Datei? Wofür ist sie in der Werkstatt relevant?
- Maße, Materialien, Hinweise zur Fertigung, wenn erkennbar
- Wenn eine Nutzer-Beschreibung vorliegt: inhaltlich einbeziehen und ergänzen
- Lücken/Unsicherheiten klar benennen
Lasse nicht anwendbare Felder in estimated_dimensions einfach weg."""


def analyze_document_file(
    file_path: str,
    *,
    file_type: str,
    title: str | None = None,
    existing_notes: str | None = None,
) -> VisionIngestResult:
    """KI-Beschreibung für Nicht-Bild-Dateien (PDF, CAD, Text, sonstige)."""
    from app.services.file_context import extract_file_context

    path = Path(file_path)
    context = extract_file_context(path, file_type)
    user_prompt = (
        f"Titel: {title or path.name}\n"
        f"Nutzer-Beschreibung (einbeziehen!): {existing_notes or '(keine)'}\n\n"
        f"{context}\n\n"
        "Erstelle die umfassende JSON-Beschreibung."
    )

    # Bevorzugt den allgemeinen LLM-Client (Anthropic oder Cursor), sonst Anthropic-direkt.
    try:
        from agents.llm_client import call_llm_json, is_llm_configured

        if is_llm_configured():
            raw = call_llm_json(_DOCUMENT_SYSTEM_PROMPT, user_prompt, max_tokens=2500)
            raw["category"] = _normalize_category(raw.get("category"))
            return VisionIngestResult(**raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dokument-Analyse via llm_client fehlgeschlagen (%s): %s", path.name, exc)

    if settings_store.is_anthropic_configured():
        try:
            raw = _call_anthropic_text(_DOCUMENT_SYSTEM_PROMPT, user_prompt)
            raw["category"] = _normalize_category(raw.get("category"))
            return VisionIngestResult(**raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dokument-Analyse via Anthropic fehlgeschlagen (%s): %s", path.name, exc)

    return VisionIngestResult(
        category="cad_reference" if file_type in ("step", "stl", "f3d", "pdf") else "unknown",
        description=(
            f"Automatische Heuristik (kein LLM konfiguriert) für '{path.name}' "
            f"({file_type}, {path.stat().st_size if path.is_file() else '?'} Bytes). "
            f"Bitte Beschreibung manuell ergänzen.\n\nKontext-Auszug:\n{context[:1500]}"
        ),
        tags=["heuristic_fallback", file_type],
    )


def repair_misclassified_asset(db: Session, asset: UnprocessedAsset) -> bool:
    """Korrigiert bekannte Fehlklassifizierungen ohne erneuten Vision-Call.
    z.B. KI-Konzeptbild fälschlich als material → product_concept + KI-Generiert."""
    vision = asset.vision_result if isinstance(asset.vision_result, dict) else {}
    category = str(vision.get("category") or "")
    desc = f"{notes_blob(asset)} {vision.get('description') or ''} {asset.title or ''}".lower()
    furniture_hints = (
        "schrank",
        "eckschrank",
        "regal",
        "möbel",
        "moebel",
        "konzept",
        "render",
        "visualisierung",
    )
    looks_like_concept = _asset_forces_product_concept(asset) or (
        category == "material" and any(h in desc for h in furniture_hints)
    )
    if not looks_like_concept:
        return False
    if category == "product_concept" and "KI-Generiert" in (asset.tags or []):
        return False

    asset.tags = list(
        dict.fromkeys([*(asset.tags or []), "KI-Generiert", "concept_image", "product_concept"])
    )
    asset.vision_result = {
        **vision,
        "category": "product_concept",
        "tags": list(dict.fromkeys([*(vision.get("tags") or []), "KI-Generiert", "product_concept"])),
    }
    db.commit()
    logger.info("Fehlklassifizierung korrigiert: asset=%s → product_concept", asset.id)
    return True


def ingest_asset(db: Session, asset: UnprocessedAsset) -> dict[str, Any]:
    """Orchestriert die vollständige Ingestion eines `unprocessed_assets`-Eintrags:
    Vision-/Text-Analyse (je nach Typ) -> DB-/Qdrant-Routing -> Status-Update.
    Die KI-Beschreibung landet in `ai_notes` (User-Text in `user_notes` bleibt erhalten
    und wird bei der Generierung einbezogen)."""
    from app.models.unprocessed_asset import AssetFileType, AssetStatus

    user_desc = effective_user_notes(asset) or ""
    try:
        if asset.file_type == AssetFileType.MANUAL or not asset.file_path:
            result = analyze_manual_entry(asset.title or "Unbenannter Eintrag", user_desc)
        elif asset.file_type == AssetFileType.IMAGE:
            hint = None
            if _asset_forces_product_concept(asset):
                hint = (
                    "Dies ist ein KI-generiertes Produktkonzept / Möbel-Render aus dem CAD-Chat. "
                    "category MUSS 'product_concept' sein (nicht material/tool). "
                    "Tag 'KI-Generiert' setzen."
                )
            result = analyze_asset_image(
                asset.file_path,
                classification_hint=hint,
                user_notes=user_desc or None,
            )
        else:
            hint_notes = user_desc
            if _asset_forces_cad_model(asset):
                hint_notes = (
                    (user_desc or "")
                    + "\n\nDies ist ein KI-generiertes 3D-Modell (STEP/STL) aus dem CAD-Chat. "
                    "category MUSS 'cad_reference' sein. Tags: KI-Generiert, generated_3d."
                ).strip()
            result = analyze_document_file(
                asset.file_path,
                file_type=asset.file_type.value,
                title=asset.title,
                existing_notes=hint_notes or None,
            )

        result = _apply_result_guards(asset, result)
        # Nur echte Werkzeuge/Rohware in strukturierte Tabellen – keine Konzepte/KI-Modelle
        allow_route = (
            result.category in ("tool", "material")
            and not _asset_forces_product_concept(asset)
            and not _asset_forces_cad_model(asset)
        )
        routing = route_vision_result(
            db,
            asset.file_path or f"manual:{asset.id}",
            result,
            allow_structured_routing=allow_route,
        )

        asset.vision_result = result.model_dump()
        if result.description.strip():
            # Metadaten-Zeilen aus bisheriger KI-/Legacy-Notiz behalten; User-Text unangetastet
            existing_ai = effective_ai_notes(asset)
            set_ai_notes(asset, _merge_notes(existing_ai, result.description))
        asset.tags = _preserve_protected_tags(asset.tags, result.tags)
        # Konzept-Assets: Tags und Kategorie hart sichern
        if _asset_forces_product_concept(asset) or result.category == "product_concept":
            asset.tags = list(
                dict.fromkeys([*(asset.tags or []), "KI-Generiert", "concept_image", "product_concept"])
            )
            if isinstance(asset.vision_result, dict):
                asset.vision_result = {**asset.vision_result, "category": "product_concept"}
        # KI-3D-Modelle aus dem Chat
        if _asset_forces_cad_model(asset) or result.category == "cad_reference":
            if _asset_forces_cad_model(asset):
                asset.tags = list(
                    dict.fromkeys(
                        [*(asset.tags or []), "KI-Generiert", "generated_3d", "cad_model", "from_chat"]
                    )
                )
                if isinstance(asset.vision_result, dict):
                    asset.vision_result = {**asset.vision_result, "category": "cad_reference"}
        asset.status = AssetStatus.INDEXED
        asset.error_message = None
        from datetime import datetime, timezone

        asset.processed_at = datetime.now(timezone.utc)
        db.commit()

        return {"asset_id": str(asset.id), "status": asset.status.value, **routing}

    except Exception as exc:  # noqa: BLE001 - Ingestion-Fehler dürfen den Prozess nicht abbrechen
        db.rollback()
        logger.error("Ingestion fehlgeschlagen für Asset %s: %s", asset.id, exc)
        asset.status = AssetStatus.FAILED
        asset.error_message = str(exc)
        db.commit()
        return {"asset_id": str(asset.id), "status": asset.status.value, "error": str(exc)}

