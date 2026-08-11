"""Inventory & Data Manager – Werkstatt-Integrator (SPEC Kap. 3.2.3, 3.4 Loop 1, 3.6).

Nutzt Fräser-/Material-Tabellen UND die beschriebene Asset-Bibliothek
(`unprocessed_assets.notes` / vision_result), damit Konzepte und 3D-Teile an
Referenzdateien angepasst werden können (Nutzer-Feedback).
"""

import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.db.postgres import SessionLocal
from app.db.qdrant import WORKSHOP_KNOWLEDGE_COLLECTION, get_qdrant_client
from app.models.stock_material import StockMaterial
from app.models.tool import Tool, ToolStatus
from app.models.unprocessed_asset import AssetStatus, UnprocessedAsset

logger = logging.getLogger(__name__)

TOOL_DIAMETER_TOLERANCE_MM = 0.5


def _find_matching_tool(db: Session, requested_diameter_mm: float | None) -> dict | None:
    tools = db.execute(select(Tool).where(Tool.status != ToolStatus.ABGEBROCHEN)).scalars().all()
    if not tools:
        return None

    if requested_diameter_mm is None:
        best = tools[0]
        diff = 0.0
    else:
        best = min(tools, key=lambda t: abs(float(t.diameter_mm) - requested_diameter_mm))
        diff = abs(float(best.diameter_mm) - requested_diameter_mm)

    return {
        "id": str(best.id),
        "name": best.name,
        "diameter_mm": float(best.diameter_mm),
        "max_rpm": best.max_rpm,
        "exact_match": diff <= TOOL_DIAMETER_TOLERANCE_MM,
    }


def _find_matching_stock(db: Session, material_type: str | None, plate_thickness_mm: float | None) -> dict | None:
    query = select(StockMaterial)
    if material_type:
        query = query.where(StockMaterial.material_type.ilike(f"%{material_type}%"))
    materials = db.execute(query).scalars().all()

    if not materials:
        materials = db.execute(select(StockMaterial)).scalars().all()
        if not materials:
            return None

    def _thickness_diff(material: StockMaterial) -> float:
        if plate_thickness_mm is None:
            return 0.0
        dims = material.dimensions_xyz_mm or {}
        return abs(float(dims.get("z", 0)) - plate_thickness_mm)

    best = min(materials, key=_thickness_diff)
    return {
        "id": str(best.id),
        "material_type": best.material_type,
        "dimensions_xyz_mm": best.dimensions_xyz_mm,
        "grain_direction": best.grain_direction,
    }


def _fetch_relevant_rules(limit: int = 5) -> list[str]:
    """Best-effort Abruf gelernter Faustregeln aus Qdrant (SPEC Kap. 2.2, 2.4)."""
    try:
        client = get_qdrant_client()
        points, _ = client.scroll(collection_name=WORKSHOP_KNOWLEDGE_COLLECTION, limit=limit, with_payload=True)
        return [
            payload_text
            for point in points
            if point.payload and (payload_text := point.payload.get("rule") or point.payload.get("text"))
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Konnte workshop_knowledge nicht abfragen: %s", exc)
        return []


def _keywords_from_part(part: dict) -> list[str]:
    tokens: list[str] = []
    name = (part.get("name") or "").strip()
    if name:
        tokens.append(name)
    constraints = part.get("material_tool_constraints") or {}
    if constraints.get("material_type"):
        tokens.append(str(constraints["material_type"]))
    for feature in part.get("manufacturing_features") or []:
        if isinstance(feature, str) and feature.strip():
            tokens.append(feature.strip())
        elif isinstance(feature, dict) and feature.get("name"):
            tokens.append(str(feature["name"]))
    # Einzelwörter >= 3 Zeichen
    words: list[str] = []
    for token in tokens:
        words.extend(re.findall(r"[A-Za-zÄÖÜäöüß0-9]{3,}", token))
    # dedupe, case-insensitive
    seen: set[str] = set()
    result: list[str] = []
    for w in words:
        key = w.lower()
        if key not in seen:
            seen.add(key)
            result.append(w)
    return result[:12]


def _asset_description(asset: UnprocessedAsset) -> str:
    if asset.notes and asset.notes.strip():
        return asset.notes.strip()
    vision = asset.vision_result or {}
    if isinstance(vision, dict) and vision.get("description"):
        return str(vision["description"]).strip()
    return ""


def _fetch_relevant_assets(
    db: Session,
    part: dict,
    *,
    conversation_id: str | None = None,
    limit: int = 8,
) -> list[dict]:
    """Lädt indizierte Assets inkl. Beschreibung für den 3D-Builder-Kontext.

    Priorität: KI-Modelle/Konzepte derselben Unterhaltung, dann Keyword-Match,
    dann neueste beschriebene Assets. So erkennt der Inventory Manager
    Chat-generierte STEP/STL (Tags KI-Generiert / generated_3d / conversation:…).
    """
    keywords = _keywords_from_part(part)
    base = (
        select(UnprocessedAsset)
        .where(UnprocessedAsset.status == AssetStatus.INDEXED)
        .order_by(UnprocessedAsset.processed_at.desc().nullslast())
    )

    assets: list[UnprocessedAsset] = []
    seen: set = set()

    def _add(rows: list[UnprocessedAsset]) -> None:
        for asset in rows:
            if asset.id in seen:
                continue
            seen.add(asset.id)
            assets.append(asset)
            if len(assets) >= limit:
                return

    # 1) Dieselbe Chat-Unterhaltung (Konzept + generierte 3D-Modelle)
    if conversation_id:
        conv_tag = f"conversation:{conversation_id}"
        # JSONB contains – PostgreSQL
        try:
            from sqlalchemy import cast, String

            chat_rows = db.execute(
                base.where(
                    or_(
                        UnprocessedAsset.notes.ilike(f"%{conversation_id}%"),
                        cast(UnprocessedAsset.tags, String).ilike(f"%{conv_tag}%"),
                        cast(UnprocessedAsset.tags, String).ilike("%generated_3d%"),
                    )
                ).limit(limit)
            ).scalars().all()
            # Prefer exact conversation match first
            exact = [
                a
                for a in chat_rows
                if conv_tag in (a.tags or []) or (conversation_id in (a.notes or ""))
            ]
            _add(exact)
            if len(assets) < limit:
                _add([a for a in chat_rows if a not in exact])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chat-Asset-Suche fehlgeschlagen: %s", exc)

    # 2) Keyword-Match auf Titel/Notizen
    if len(assets) < limit and keywords:
        filters = []
        for kw in keywords:
            like = f"%{kw}%"
            filters.append(UnprocessedAsset.title.ilike(like))
            filters.append(UnprocessedAsset.notes.ilike(like))
        matched = db.execute(
            base.where(UnprocessedAsset.notes.isnot(None)).where(or_(*filters)).limit(limit)
        ).scalars().all()
        _add(matched)

    # 3) Auffüllen mit neuesten beschriebenen / KI-Assets
    if len(assets) < limit:
        from sqlalchemy import cast, String

        extra = db.execute(
            base.where(
                or_(
                    UnprocessedAsset.notes.isnot(None),
                    cast(UnprocessedAsset.tags, String).ilike("%KI-Generiert%"),
                )
            ).limit(limit)
        ).scalars().all()
        _add(extra)

    results: list[dict] = []
    for asset in assets[:limit]:
        description = _asset_description(asset)
        if not description and "KI-Generiert" not in (asset.tags or []):
            continue
        if not description:
            description = (
                f"KI-generiertes Inventar-Asset ({asset.file_type.value if asset.file_type else 'datei'}): "
                f"{asset.title or asset.file_path}"
            )
        vision = asset.vision_result if isinstance(asset.vision_result, dict) else {}
        results.append(
            {
                "id": str(asset.id),
                "title": asset.title or asset.file_path,
                "file_type": asset.file_type.value if asset.file_type else None,
                "file_path": asset.file_path,
                "category": vision.get("category"),
                "tags": asset.tags or vision.get("tags") or [],
                "description": truncate(description, 2000),
                "from_chat": "from_chat" in (asset.tags or [])
                or any(str(t).startswith("conversation:") for t in (asset.tags or [])),
                "ai_generated": "KI-Generiert" in (asset.tags or []),
            }
        )
    return results


def inventory_manager_node(state: AgentState) -> AgentState:
    contract = state.get("requirements_contract", {})
    parts = list(contract.get("parts", []))
    idx = state.get("current_part_index", 0)
    part = parts[idx] if idx < len(parts) else {}
    part_label = f"Teil {idx + 1}/{len(parts)} ('{part.get('name', 'Unbenannt')}')"
    constraints = part.get("material_tool_constraints", {})
    conversation_id = state.get("conversation_id")

    db = SessionLocal()
    try:
        tool_match = _find_matching_tool(db, constraints.get("tool_diameter_mm"))
        stock_match = _find_matching_stock(db, constraints.get("material_type"), constraints.get("plate_thickness_mm"))
        relevant_assets = _fetch_relevant_assets(db, part, conversation_id=conversation_id)
    finally:
        db.close()

    relevant_rules = _fetch_relevant_rules()

    updated_part = dict(part)
    gap_analysis = list(part.get("gap_analysis", []))
    refinement_request = None
    escalation_reasons: list[str] = []

    if tool_match is None:
        escalation_reasons.append(
            f"Kein Fräser im Werkzeug-Inventar vorhanden (angefordert: {constraints.get('tool_diameter_mm')}mm)."
        )
    elif not tool_match["exact_match"]:
        gap_analysis.append(
            f"Kein exakter {constraints.get('tool_diameter_mm')}mm-Fräser vorhanden – "
            f"nächstgelegene Option: '{tool_match['name']}' ({tool_match['diameter_mm']}mm)."
        )
        material_tool_constraints = dict(updated_part.get("material_tool_constraints", {}))
        material_tool_constraints["tool_diameter_mm"] = tool_match["diameter_mm"]
        updated_part["material_tool_constraints"] = material_tool_constraints
        refinement_request = {
            "from_agent": "inventory_manager",
            "target": "concept_builder",
            "reason": "tool_substitution",
        }

    if stock_match is None:
        escalation_reasons.append(
            f"Kein Lagermaterial für '{constraints.get('material_type', 'unbekannt')}' verfügbar."
        )

    if relevant_assets:
        gap_analysis.append(
            f"{len(relevant_assets)} Referenz-Asset(s) aus der Inventar-Bibliothek für den 3D-Builder geladen."
        )

    updated_part["gap_analysis"] = gap_analysis
    if idx < len(parts):
        parts[idx] = updated_part
    updated_contract = dict(contract)
    updated_contract["parts"] = parts

    context = {
        "sender_agent": "Inventory_Manager",
        "recipient_agent": "3D_Builder",
        "payload_type": "CONTEXT_INJECTION",
        "data": {
            "recommended_tools": [tool_match] if tool_match else [],
            "stock_constraints": stock_match or {},
            "relevant_rules": relevant_rules,
            "relevant_assets": relevant_assets,
        },
    }

    updates: AgentState = {
        "stock_and_tool_context": context,
        "requirements_contract": updated_contract,
        "current_agent": "inventory_manager",
        "messages": [
            {
                "role": "assistant",
                "content": (
                    f"[Inventory Manager] {part_label}: Werkzeug/Material abgeglichen. "
                    f"Regeln: {len(relevant_rules)}, Referenz-Assets: {len(relevant_assets)}."
                ),
            }
        ],
        "agent_transcript": [
            make_entry(
                "inventory_manager",
                "context_injection",
                f"{part_label}: Inventar + {len(relevant_assets)} Asset-Beschreibung(en) → 3D Builder",
                detail={
                    "part_label": part_label,
                    "tool_match": tool_match,
                    "stock_match": stock_match,
                    "rules_count": len(relevant_rules),
                    "assets_count": len(relevant_assets),
                    "asset_titles": [a.get("title") for a in relevant_assets],
                    "conversation_id": conversation_id,
                    "chat_linked_assets": sum(1 for a in relevant_assets if a.get("from_chat")),
                    "escalation_reasons": escalation_reasons,
                },
                to_agent="builder_3d",
            )
        ],
    }

    # Fehlendes Werkzeug/Material: Warnung loggen, aber Pipeline fortsetzen
    # (keine User-Eskalation – Agenten arbeiten mit best-effort Context weiter).
    if escalation_reasons:
        gap_analysis.extend(escalation_reasons)
        if idx < len(parts):
            updated_part["gap_analysis"] = gap_analysis
            parts[idx] = updated_part
            updated_contract["parts"] = parts
            updates["requirements_contract"] = updated_contract
        updates["messages"] = list(updates["messages"]) + [
            {
                "role": "assistant",
                "content": (
                    f"[Inventory Manager] {part_label}: Inventar-Lücken – "
                    "arbeite trotzdem weiter: " + " ".join(escalation_reasons)
                ),
            }
        ]
        updates["agent_transcript"] = list(updates["agent_transcript"]) + [
            make_entry(
                "inventory_manager",
                "continue_despite_gaps",
                "Inventar-Lücken – keine User-Eskalation, weiter an 3D Builder",
                detail={"gaps": escalation_reasons},
                to_agent="builder_3d",
            )
        ]
    else:
        updates["refinement_request"] = refinement_request

    return updates
