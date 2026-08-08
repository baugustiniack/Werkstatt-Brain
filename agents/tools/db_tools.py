"""LangGraph-Tools für Werkzeug-/Material-Inventar (PostgreSQL) und semantische
CAD-Snippet-Suche (Qdrant) – SPEC Kap. 2.1, 2.2, 3.2.3, 4.2.

Diese Tools sind bewusst als eigenständige, wiederverwendbare Bausteine
implementiert (Plain-Function + `@tool`-Wrapper), sodass sie sowohl direkt
von Nodes aufgerufen als auch später an ein LLM via `bind_tools()` (Kap. 3.2.4)
gebunden werden können.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool
from sqlalchemy import select

from app.db.postgres import SessionLocal
from app.db.qdrant import CAD_SNIPPETS_COLLECTION, get_qdrant_client
from app.models.stock_material import StockMaterial
from app.models.tool import Tool, ToolStatus

logger = logging.getLogger(__name__)


def _tool_to_dict(tool_row: Tool) -> dict:
    return {
        "id": str(tool_row.id),
        "name": tool_row.name,
        "diameter_mm": float(tool_row.diameter_mm),
        "flute_length_mm": float(tool_row.flute_length_mm) if tool_row.flute_length_mm is not None else None,
        "max_rpm": tool_row.max_rpm,
        "feed_rate_mm_min": (
            float(tool_row.feed_rate_mm_min) if tool_row.feed_rate_mm_min is not None else None
        ),
        "status": tool_row.status.value,
    }


def _material_to_dict(material: StockMaterial) -> dict:
    return {
        "id": str(material.id),
        "material_type": material.material_type,
        "dimensions_xyz_mm": material.dimensions_xyz_mm,
        "grain_direction": material.grain_direction,
        "notes": material.notes,
    }


@tool
def find_tools_by_diameter(diameter_mm: float, tolerance_mm: float = 1.0) -> list[dict]:
    """Sucht einsatzbereite Fräser (z. B. Schaftfräser, V-Nut-Fräser) im
    Werkzeug-Inventar (Tabelle `tools`), deren Durchmesser innerhalb von
    `tolerance_mm` um `diameter_mm` liegt. Werkzeuge mit Status 'abgebrochen'
    werden ausgeschlossen. Gibt eine Liste passender Werkzeug-Datensätze zurück."""
    db = SessionLocal()
    try:
        rows = db.execute(select(Tool).where(Tool.status != ToolStatus.ABGEBROCHEN)).scalars().all()
    finally:
        db.close()

    matches = [t for t in rows if abs(float(t.diameter_mm) - diameter_mm) <= tolerance_mm]
    matches.sort(key=lambda t: abs(float(t.diameter_mm) - diameter_mm))
    return [_tool_to_dict(t) for t in matches]


@tool
def list_all_tools() -> list[dict]:
    """Listet das komplette Werkzeug-Inventar (Tabelle `tools`) unabhängig vom
    Status auf, inkl. Durchmesser, Drehzahl- und Vorschub-Empfehlungen."""
    db = SessionLocal()
    try:
        rows = db.execute(select(Tool)).scalars().all()
    finally:
        db.close()
    return [_tool_to_dict(t) for t in rows]


@tool
def find_stock_materials(
    material_type: str | None = None,
    min_thickness_mm: float | None = None,
) -> list[dict]:
    """Sucht verfügbare Rohmaterial-/Reststück-Bestände (Tabelle `stock_materials`),
    optional gefiltert nach Materialtyp (z. B. 'Multiplex', 'MDF') und einer
    Mindest-Plattenstärke in mm (geprüft gegen die z-Dimension)."""
    db = SessionLocal()
    try:
        rows = db.execute(select(StockMaterial)).scalars().all()
    finally:
        db.close()

    def matches(material: StockMaterial) -> bool:
        if material_type and material_type.lower() not in material.material_type.lower():
            return False
        if min_thickness_mm is not None:
            thickness = material.dimensions_xyz_mm.get("z", 0)
            if thickness < min_thickness_mm:
                return False
        return True

    return [_material_to_dict(m) for m in rows if matches(m)]


@tool
def search_cad_snippets(query_text: str, limit: int = 5) -> list[dict]:
    """Semantische Suche nach passenden build123d-Code-Mustern (z. B. Schwalben-
    schwanz-Zinkung, T-Nut, Zapfenverbindung) in der Qdrant-Collection
    `cad_snippets`.

    Hinweis: Ohne konfigurierte Embedding-Pipeline (bge-m3/nomic-embed-text,
    SPEC Kap. 2.2) wird aktuell eine einfache Payload-Textsuche (scroll +
    Substring-Filter) als Platzhalter verwendet; sobald ein Embedding-Modell
    verfügbar ist, sollte dies durch echte Vektor-Ähnlichkeitssuche
    (`client.search(...)`) ersetzt werden."""
    try:
        client = get_qdrant_client()
        points, _ = client.scroll(collection_name=CAD_SNIPPETS_COLLECTION, limit=100, with_payload=True)
    except Exception as exc:  # noqa: BLE001 - Tool darf den Graphen nie hart abbrechen
        logger.warning("Qdrant-Suche in %s fehlgeschlagen: %s", CAD_SNIPPETS_COLLECTION, exc)
        return []

    query_lower = (query_text or "").lower().strip()
    results = []
    for point in points:
        payload = point.payload or {}
        haystack = " ".join(str(v) for v in payload.values()).lower()
        if not query_lower or query_lower in haystack:
            results.append({"id": str(point.id), "payload": payload})

    return results[:limit]
