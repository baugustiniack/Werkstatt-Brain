"""Montage Manager – Passung, Montierbarkeit und Montage-Anleitung.

Läuft nachdem alle Teile erfolgreich validiert sind. Prüft Passung, schreibt eine
praxisnahe Montageanleitung und bezieht Werkzeuge aus der Inventar-DB ein
(sonst Empfehlungen für fehlende Werkzeuge).
"""

from __future__ import annotations

import logging
from typing import Any

from agents.llm_client import call_llm_json, is_llm_configured
from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_agent_guidance

logger = logging.getLogger(__name__)

_MONTAGE_SYSTEM = """Du bist Montage-Manager einer CNC-/Holzwerkstatt.
Prüfe Passung/Montierbarkeit der Bauteile und schreibe eine vollständige Montage-Anleitung.
Beziehe vorhandene Werkzeuge aus dem Inventar ein. Fehlen Werkzeuge, empfiehl konkrete Neuanschaffungen.

Antworte NUR mit JSON:
{
  "assemblable": boolean,
  "summary": string,
  "fit_issues": [{"parts": [string, ...], "severity": "critical"|"warning"|"info", "description": string}],
  "interfaces": [{"from_part": string, "to_part": string, "type": string, "ok": boolean, "notes": string}],
  "tools_from_inventory": [
    {"name": string, "id": string | null, "role": string, "from_inventory": true}
  ],
  "tools_recommended": [
    {"name": string, "reason": string, "priority": "required"|"nice_to_have", "approx_spec": string}
  ],
  "assembly_steps": [
    {
      "step": number,
      "title": string,
      "action": string,
      "parts": [string, ...],
      "tools": [string, ...],
      "notes": string,
      "safety": string
    }
  ],
  "assembly_manual": {
    "title": string,
    "intro": string,
    "safety_notes": [string, ...],
    "required_tools": [string, ...],
    "optional_tools": [string, ...],
    "prep": [string, ...],
    "steps_markdown": string,
    "tips": [string, ...],
    "checklist": [string, ...]
  },
  "recommendations": [string, ...]
}

Regeln:
- Deutsch, konkret, praxisnah (Möbelbau/CNC/Handmontage).
- assembly_manual.steps_markdown: nummerierte Anleitung als Markdown (## / 1. 2. …).
- tools_from_inventory: nur Einträge, die im gelieferten Inventar vorkommen (Name/ID übernehmen).
- tools_recommended: nur wenn Inventar die Montage nicht abdeckt; spezifiziere sinnvoll (z.B. „Akkuschrauber“, „Dübel 8mm“, „Zwingen 600mm“).
- Bei nur einem Teil: kurze Nachbearbeitungs-/Endanleitung, assemblable=true.
- critical nur bei echten Passungs-/Montageblockern.
"""


def _load_montage_tools() -> list[dict[str, Any]]:
    """Werkzeuge und montage-relevante Assets aus der Inventar-DB."""
    items: list[dict[str, Any]] = []
    try:
        from app.db.postgres import SessionLocal
        from app.models.tool import Tool
        from app.models.unprocessed_asset import UnprocessedAsset

        db = SessionLocal()
        try:
            for tool in db.query(Tool).limit(120).all():
                items.append(
                    {
                        "kind": "tool",
                        "id": str(tool.id),
                        "name": tool.name,
                        "diameter_mm": float(tool.diameter_mm) if tool.diameter_mm is not None else None,
                        "flute_length_mm": float(tool.flute_length_mm)
                        if tool.flute_length_mm is not None
                        else None,
                        "status": getattr(tool.status, "value", str(tool.status)),
                    }
                )
            assets = (
                db.query(UnprocessedAsset)
                .order_by(UnprocessedAsset.discovered_at.desc())
                .limit(80)
                .all()
            )
            keywords = (
                "zwinge",
                "schraub",
                "bohrer",
                "dübel",
                "duebel",
                "hammer",
                "schlägel",
                "schlaegel",
                "winkel",
                "wasserwaage",
                "montage",
                "presse",
                "klemm",
                "raspel",
                "feile",
                "schleif",
                "handwerkzeug",
                "werkzeug",
            )
            for asset in assets:
                tags = asset.tags or []
                title = (asset.title or asset.file_path or "")[:120]
                blob = f"{title} {' '.join(tags)} {(asset.notes or '')[:200]}".lower()
                if any(k in blob for k in keywords) or any(
                    t.lower() in ("werkzeug", "tool", "montage", "handwerkzeug") for t in tags
                ):
                    items.append(
                        {
                            "kind": "asset",
                            "id": str(asset.id),
                            "name": title or str(asset.id)[:8],
                            "file_type": asset.file_type.value if asset.file_type else None,
                            "tags": tags[:8],
                            "notes": (asset.notes or "")[:240],
                        }
                    )
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Montage-Inventar konnte nicht geladen werden: %s", exc)
    return items


def _part_snapshot(part: dict[str, Any]) -> dict[str, Any]:
    contract = part.get("part_contract") if isinstance(part.get("part_contract"), dict) else {}
    geom = contract.get("functional_geometry") or {}
    mats = contract.get("material_tool_constraints") or {}
    return {
        "name": part.get("name") or contract.get("name") or "Teil",
        "dimensions_mm": (geom.get("dimensions_mm") if isinstance(geom, dict) else None),
        "material": mats.get("material_type") if isinstance(mats, dict) else None,
        "features": contract.get("manufacturing_features") or [],
        "position_xy_mm": contract.get("position_xy_mm"),
        "has_exports": bool((part.get("sandbox_result") or {}).get("export_paths")),
        "sandbox_ok": (part.get("sandbox_result") or {}).get("status") == "SUCCESS",
    }


def _format_manual_markdown(manual: dict[str, Any], title: str) -> str:
    lines: list[str] = [f"# {manual.get('title') or f'Montageanleitung: {title}'}"]
    if manual.get("intro"):
        lines.extend(["", str(manual["intro"])])
    safety = manual.get("safety_notes") or []
    if safety:
        lines.extend(["", "## Sicherheit"])
        lines.extend(f"- {s}" for s in safety)
    req = manual.get("required_tools") or []
    opt = manual.get("optional_tools") or []
    if req or opt:
        lines.extend(["", "## Werkzeuge"])
        if req:
            lines.append("**Benötigt:**")
            lines.extend(f"- {t}" for t in req)
        if opt:
            lines.append("**Optional:**")
            lines.extend(f"- {t}" for t in opt)
    prep = manual.get("prep") or []
    if prep:
        lines.extend(["", "## Vorbereitung"])
        lines.extend(f"- {p}" for p in prep)
    steps_md = (manual.get("steps_markdown") or "").strip()
    if steps_md:
        lines.extend(["", "## Montageablauf", steps_md])
    tips = manual.get("tips") or []
    if tips:
        lines.extend(["", "## Tipps"])
        lines.extend(f"- {t}" for t in tips)
    checklist = manual.get("checklist") or []
    if checklist:
        lines.extend(["", "## Checkliste"])
        lines.extend(f"- [ ] {c}" for c in checklist)
    return "\n".join(lines).strip()


def _heuristic_montage(
    completed: list[dict[str, Any]],
    title: str,
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    snaps = [_part_snapshot(p) for p in completed]
    tool_names = [t.get("name") for t in inventory if t.get("kind") == "tool"][:6]
    asset_names = [t.get("name") for t in inventory if t.get("kind") == "asset"][:4]
    inv_tools = [
        {"name": n, "id": None, "role": "aus Inventar", "from_inventory": True}
        for n in (tool_names[:3] or asset_names[:2])
    ]
    recommended: list[dict[str, Any]] = []
    if not inventory:
        recommended = [
            {
                "name": "Schraubzwingen (mind. 2×)",
                "reason": "Teile während der Montage fixieren",
                "priority": "required",
                "approx_spec": "Spannweite ≥ 400 mm",
            },
            {
                "name": "Akkuschrauber",
                "reason": "Schraubenverbindungen setzen",
                "priority": "required",
                "approx_spec": "mit Bits PH2 / Torx",
            },
        ]

    if len(snaps) <= 1:
        name = snaps[0]["name"] if snaps else "Werkstück"
        steps = [
            {
                "step": 1,
                "title": "Nachbearbeitung",
                "action": f"Teil „{name}“ entnehmen, entgraten und Oberfläche prüfen",
                "parts": [name],
                "tools": [t["name"] for t in inv_tools] or ["Schleifpapier", "Entgrater"],
                "notes": "Einteiliges Werkstück – keine Mehrteil-Montage",
                "safety": "Schutzbrille bei Schleifen",
            }
        ]
        manual = {
            "title": f"Montageanleitung: {title}",
            "intro": f"„{title}“ besteht aus einem Teil („{name}“). Fokus: Nachbearbeitung und Endkontrolle.",
            "safety_notes": ["Schutzbrille bei Schleif-/Bohrarbeiten", "Werkstück sicher fixieren"],
            "required_tools": [t["name"] for t in inv_tools] or ["Schleifpapier"],
            "optional_tools": ["Wasserwaage"],
            "prep": ["Arbeitsfläche säubern", "Teil auf Beschädigungen prüfen"],
            "steps_markdown": (
                f"1. Teil „{name}“ bereitstellen.\n"
                "2. Kanten entgraten und Oberfläche glätten.\n"
                "3. Maße und Oberfläche gegen Konzept prüfen.\n"
            ),
            "tips": ["Fehlende Handwerkzeuge bei Bedarf nachbeschaffen."],
            "checklist": ["Kanten gratfrei", "Maße ok", "Oberfläche ok"],
        }
        return {
            "assemblable": True,
            "summary": f"„{title}“: einteilig („{name}“) – Nachbearbeitungsanleitung erstellt.",
            "fit_issues": [],
            "interfaces": [],
            "tools_from_inventory": inv_tools,
            "tools_recommended": recommended,
            "assembly_steps": steps,
            "assembly_manual": manual,
            "recommendations": [],
        }

    issues: list[dict[str, Any]] = []
    interfaces: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    default_tools = [t["name"] for t in inv_tools] or ["Zwingen", "Akkuschrauber"]
    for i, snap in enumerate(snaps):
        steps.append(
            {
                "step": i + 1,
                "title": f"Teil {i + 1} ausrichten",
                "action": f"Teil „{snap['name']}“ bereitstellen und ausrichten",
                "parts": [snap["name"]],
                "tools": default_tools[:2],
                "notes": f"Maße: {snap.get('dimensions_mm') or 'unbekannt'}",
                "safety": "",
            }
        )
        if i + 1 < len(snaps):
            nxt = snaps[i + 1]
            interfaces.append(
                {
                    "from_part": snap["name"],
                    "to_part": nxt["name"],
                    "type": "Anschluss / Stoß",
                    "ok": True,
                    "notes": "Heuristik: Maße manuell gegenprüfen",
                }
            )
        if not snap.get("sandbox_ok"):
            issues.append(
                {
                    "parts": [snap["name"]],
                    "severity": "critical",
                    "description": f"Kein erfolgreicher 3D-Export für „{snap['name']}“.",
                }
            )

    steps.append(
        {
            "step": len(steps) + 1,
            "title": "Zusammenfügen",
            "action": "Alle Teile zusammenfügen und Verbindungen prüfen",
            "parts": [s["name"] for s in snaps],
            "tools": default_tools,
            "notes": "Schrauben/Dübel/Nut-Feder je Konzept",
            "safety": "Nicht unter hängenden Lasten arbeiten",
        }
    )
    assemblable = not any(i.get("severity") == "critical" for i in issues)
    step_lines = "\n".join(
        f"{s['step']}. **{s.get('title') or 'Schritt'}:** {s['action']}"
        + (f" (Werkzeuge: {', '.join(s.get('tools') or [])})" if s.get("tools") else "")
        for s in steps
    )
    manual = {
        "title": f"Montageanleitung: {title}",
        "intro": f"Montage von {len(snaps)} Teilen für „{title}“ (Heuristik).",
        "safety_notes": ["Schutzbrille", "Teile vor dem Verschrauben mit Zwingen sichern"],
        "required_tools": default_tools,
        "optional_tools": ["Winkel", "Wasserwaage"],
        "prep": [
            "Alle Teile beschriften und in Reihenfolge legen",
            "Inventar-Werkzeuge bereitlegen",
        ],
        "steps_markdown": step_lines,
        "tips": [
            "Trockene Probe (ohne Leim/Schrauben) vor Endmontage",
            "Anschlussmaße der Nachbarteile gegenprüfen",
        ],
        "checklist": [f"Teil „{s['name']}“ verbaut" for s in snaps] + ["Verbindungen fest", "Rechtwinkligkeit ok"],
    }
    return {
        "assemblable": assemblable,
        "summary": (
            f"„{title}“: {len(snaps)} Teile geprüft – "
            + ("montierbar; Anleitung erstellt." if assemblable else "kritische Passungsprobleme.")
        ),
        "fit_issues": issues,
        "interfaces": interfaces,
        "tools_from_inventory": inv_tools,
        "tools_recommended": recommended,
        "assembly_steps": steps,
        "assembly_manual": manual,
        "recommendations": [
            "Anschlussmaße der Nachbarteile im Konzept gegenprüfen",
            "Montage-Reihenfolge an Fertigungsplan anpassen",
        ],
    }


def montage_manager_node(state: AgentState) -> AgentState:
    contract = state.get("requirements_contract") or {}
    title = contract.get("project_title") or "Projekt"
    completed = list(state.get("completed_parts") or [])
    mfg = state.get("manufacturing_plan") or {}
    vv = state.get("vv_requirements") or {}
    advisory = state.get("advisory_notes") or ""
    guidance = get_agent_guidance("montage_manager")
    inventory = _load_montage_tools()

    # Inventar-Kontext aus dem zuletzt fertigen Teil (falls vorhanden)
    stock_ctx = None
    for part in reversed(completed):
        if part.get("stock_and_tool_context"):
            stock_ctx = part.get("stock_and_tool_context")
            break
    if stock_ctx is None:
        stock_ctx = state.get("stock_and_tool_context")

    snaps = [_part_snapshot(p) for p in completed]
    if is_llm_configured() and snaps:
        try:
            result = call_llm_json(
                _MONTAGE_SYSTEM,
                (
                    f"Projekt: {title}\n"
                    f"User-Prompt: {(state.get('user_prompt') or '')[:800]}\n"
                    f"Teile ({len(snaps)}):\n{snaps}\n"
                    f"Fertigung (Kurz): { {k: mfg.get(k) for k in ('feasible', 'summary', 'work_steps', 'machines_needed') if mfg} }\n"
                    f"V&V: { {k: vv.get(k) for k in ('title', 'summary', 'phase') if vv} }\n"
                    f"Stock/Tool-Kontext: {stock_ctx}\n"
                    f"Inventar-Werkzeuge/Assets ({len(inventory)}):\n{inventory[:50]}\n"
                    f"Advisory: {advisory}\n"
                    f"Guidance: {guidance or '(keine)'}\n"
                ),
                max_tokens=3500,
            )
            if not isinstance(result, dict):
                raise ValueError("Montage-LLM lieferte kein Dict")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Montage LLM fehlgeschlagen: %s", exc)
            result = _heuristic_montage(completed, title, inventory)
    else:
        result = _heuristic_montage(completed, title, inventory)

    assemblable = bool(result.get("assemblable", True))
    issues = result.get("fit_issues") or []
    critical = [i for i in issues if isinstance(i, dict) and i.get("severity") == "critical"]
    steps = result.get("assembly_steps") or []
    tools_inv = result.get("tools_from_inventory") or []
    tools_rec = result.get("tools_recommended") or []
    manual = result.get("assembly_manual") if isinstance(result.get("assembly_manual"), dict) else {}
    if not manual:
        # Fallback: minimale Anleitung aus Schritten ableiten
        manual = {
            "title": f"Montageanleitung: {title}",
            "intro": result.get("summary") or "",
            "safety_notes": [],
            "required_tools": [t.get("name") for t in tools_inv if isinstance(t, dict)],
            "optional_tools": [],
            "prep": [],
            "steps_markdown": "\n".join(
                f"{s.get('step', i+1)}. {s.get('action') or s.get('title') or ''}"
                for i, s in enumerate(steps)
                if isinstance(s, dict)
            ),
            "tips": result.get("recommendations") or [],
            "checklist": [],
        }
        result["assembly_manual"] = manual

    manual_md = _format_manual_markdown(manual, title)
    note = (
        f"Montage {'OK' if assemblable else 'kritisch'}: {len(snaps)} Teil(e), "
        f"{len(steps)} Schritte, {len(tools_inv)} Inventar-Werkzeug(e)"
        + (f", {len(tools_rec)} Empfehlung(en)" if tools_rec else "")
        + (f", {len(critical)} kritische(r) Fund(e)" if critical else "")
        + " · Anleitung erstellt."
    )

    assembly_plan = {
        "assemblable": assemblable,
        "steps": steps,
        "interfaces": result.get("interfaces") or [],
        "tools_from_inventory": tools_inv,
        "tools_recommended": tools_rec,
        "manual": manual,
        "manual_markdown": manual_md,
    }

    updates: AgentState = {
        "montage_result": result,
        "assembly_plan": assembly_plan,
        "assembly_manual": {
            **manual,
            "markdown": manual_md,
            "tools_from_inventory": tools_inv,
            "tools_recommended": tools_rec,
        },
        "montage_assessed": True,
        "refinement_request": None,
        "current_agent": "montage_manager",
        "messages": [{"role": "assistant", "content": f"[Montage Manager] {note}"}],
        "agent_transcript": [
            make_entry(
                "montage_manager",
                "assembly_manual",
                note,
                detail={
                    "assemblable": assemblable,
                    "parts_count": len(snaps),
                    "fit_issues": issues,
                    "assembly_steps_count": len(steps),
                    "tools_from_inventory": tools_inv,
                    "tools_recommended": tools_rec,
                    "inventory_seen": len(inventory),
                    "manual_title": manual.get("title"),
                    "manual_preview": truncate(manual_md, 800),
                    "summary": truncate(str(result.get("summary") or ""), 500),
                    "recommendations": result.get("recommendations"),
                },
                to_agent="supervisor",
            )
        ],
    }

    return updates
