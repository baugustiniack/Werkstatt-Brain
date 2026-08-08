"""3D Builder – build123d Code-Synthesizer (SPEC Kap. 3.2.4, 3.4 Loop 2).

Arbeitet auf `requirements_contract["parts"][current_part_index]` (Nutzer-
Feedback: Mehrteil-Anfragen werden sequenziell abgearbeitet).

Nutzt einen echten LLM-Call (`agents.llm_client`) zur Code-Synthese, sofern
ein Anthropic-Key konfiguriert ist, da das feste Box-Template nur einfache
Plattenteile abdeckt. Ohne Key (oder bei einem fehlgeschlagenen Call) fällt
die Funktion auf das deterministische Template zurück, damit das System auch
ohne Cloud-API lauffähig bleibt. Die topologische Konfliktprüfung
(`_check_topological_conflict`) bleibt in jedem Fall als schnelle
Vorab-Validierung aktiv.

Export-Konvention (Kap. 4): Der generierte Code exportiert die Geometrie
NICHT selbst – die Sandbox (`sandbox/run.py`) übernimmt bei Erfolg
automatisch den STEP-/STL-Export, sofern die Geometrie in der Variable
`part` (per `with BuildPart() as part:`) abgelegt wurde.
"""

import logging

from agents.llm_client import call_llm_text, extract_code_block, is_llm_configured
from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_agent_guidance

logger = logging.getLogger(__name__)

MIN_WALL_SAFETY_FACTOR = 2.0  # Radius darf max. die Hälfte der Materialstärke betragen

_BUILD123D_SYSTEM_PROMPT = """Du bist ein Experte für parametrisches CAD mit der Python-Bibliothek \
build123d. Generiere AUSSCHLIESSLICH lauffähigen Python-Code (ein einzelner Markdown-Codeblock, keine \
Erklärungen davor/danach) für EIN Werkstück gemäß der gegebenen Spezifikation.

Regeln (STRIKT einhalten):
1. `from build123d import *` als erste Zeile.
2. Die gesamte Geometrie MUSS in `with BuildPart() as part:` aufgebaut werden (Variable exakt `part`).
3. Rufe NIEMALS `export_step`/`export_stl` selbst auf – das übernimmt die Sandbox automatisch nach \
erfolgreicher Ausführung.
4. Nutze nur Standard-build123d-API (Box, Cylinder, Locations, Mode.SUBTRACT, fillet, chamfer, \
Rectangle+extrude, Sketch-Operationen etc.), keine externen Zusatzpakete.
5. Setze Maße/Radien exakt gemäß Spezifikation ein (in mm)."""


def _check_topological_conflict(dimensions_mm: dict, radius_mm: float | None) -> str | None:
    """Frühzeitige mathematische Plausibilitätsprüfung (Loop 2: 3D Builder ↔ Concept Builder)."""
    if not radius_mm:
        return None

    thickness = dimensions_mm.get("z", 18.0)
    if radius_mm * MIN_WALL_SAFETY_FACTOR > thickness:
        return (
            f"Radius {radius_mm}mm überschreitet die Flächenbegrenzung bei "
            f"{thickness}mm Materialstärke (Fillet-Konflikt)."
        )
    return None


def _render_build123d_code(dimensions_mm: dict, radius_mm: float | None, features: list[str]) -> str:
    x, y, z = dimensions_mm["x"], dimensions_mm["y"], dimensions_mm["z"]

    lines = [
        "from build123d import *",
        "",
        "with BuildPart() as part:",
        f"    Box({x}, {y}, {z})",
    ]

    if "Bohrung" in features or "Durchgangsbohrung" in features:
        lines.append(f"    with Locations((0, 0, {z / 2})):")
        lines.append(f"        Cylinder(radius=4.0, height={z}, mode=Mode.SUBTRACT)")

    if "Tasche" in features:
        depth = z * 0.3
        lines.append(f"    with Locations((0, 0, {z / 2 - depth / 2})):")
        lines.append(f"        Box({x * 0.5}, {y * 0.5}, {depth}, mode=Mode.SUBTRACT)")

    if radius_mm:
        lines.append(f"    fillet(part.edges(), radius={radius_mm})")

    return "\n".join(lines)


def _build123d_system_prompt() -> str:
    guidance = get_agent_guidance("builder_3d")
    if guidance:
        return f"{_BUILD123D_SYSTEM_PROMPT}\n\nZusätzliche Meta-Coach-Guidance:\n{guidance}"
    return _BUILD123D_SYSTEM_PROMPT


def _llm_generate_build123d_code(part: dict, stock_and_tool_context: dict) -> str:
    assets = ((stock_and_tool_context or {}).get("data") or {}).get("relevant_assets") or []
    asset_block = ""
    if assets:
        asset_block = (
            "\n\nReferenz-Assets aus der Inventar-Bibliothek (Beschreibungen beachten und "
            "Geometrie/Material wo sinnvoll anpassen):\n"
            f"{assets}\n"
        )
    user_prompt = (
        f"Teil-Spezifikation (JSON):\n{part}\n\n"
        f"Werkzeug-/Material-Kontext (JSON):\n{stock_and_tool_context}\n"
        f"{asset_block}\n"
        "Generiere den build123d-Code für dieses Teil."
    )
    response_text = call_llm_text(_build123d_system_prompt(), user_prompt, max_tokens=2048)
    code = extract_code_block(response_text)
    if "BuildPart() as part" not in code:
        raise ValueError("LLM-Antwort enthält keinen gültigen 'with BuildPart() as part:'-Block.")
    return code


def builder_3d_node(state: AgentState) -> AgentState:
    contract = state.get("requirements_contract", {})
    parts = contract.get("parts", [])
    idx = state.get("current_part_index", 0)
    part = parts[idx] if idx < len(parts) else {}
    part_label = f"Teil {idx + 1}/{len(parts)} ('{part.get('name', 'Unbenannt')}')"

    geometry = part.get("functional_geometry", {})
    dimensions_mm = geometry.get("dimensions_mm") or {"x": 100.0, "y": 100.0, "z": 18.0}
    radius_mm = geometry.get("radii_mm")
    features = part.get("manufacturing_features", [])

    conflict = _check_topological_conflict(dimensions_mm, radius_mm)
    if conflict:
        logger.info("3D Builder: topologischer Konflikt (%s) – Refinement Request an Concept Builder", part_label)
        return {
            "refinement_request": {
                "from_agent": "builder_3d",
                "target": "concept_builder",
                "reason": conflict,
            },
            "current_agent": "builder_3d",
            "messages": [{"role": "assistant", "content": f"[3D Builder] {part_label}: Refinement Request: {conflict}"}],
            "agent_transcript": [
                make_entry(
                    "builder_3d",
                    "topological_conflict",
                    f"{part_label}: Konflikt → Concept Builder ({conflict})",
                    detail={"part": part, "conflict": conflict},
                    to_agent="concept_builder",
                )
            ],
        }

    code: str | None = None
    generation_note = "build123d-Code generiert (Template)."
    if is_llm_configured():
        try:
            code = _llm_generate_build123d_code(part, state.get("stock_and_tool_context") or {})
            generation_note = "build123d-Code generiert (LLM)."
        except Exception as exc:  # noqa: BLE001 - LLM-Ausfall darf den Workflow nie hart stoppen
            logger.warning("LLM-Code-Generierung fehlgeschlagen, Fallback auf Template: %s", exc)

    if code is None:
        code = _render_build123d_code(dimensions_mm, radius_mm, features)

    return {
        "generated_code": code,
        "refinement_request": None,
        "current_agent": "builder_3d",
        "messages": [{"role": "assistant", "content": f"[3D Builder] {part_label}: {generation_note}"}],
        "agent_transcript": [
            make_entry(
                "builder_3d",
                "code_generated",
                f"{part_label}: {generation_note}",
                detail={
                    "part_name": part.get("name"),
                    "dimensions_mm": dimensions_mm,
                    "features": features,
                    "llm_used": generation_note.endswith("(LLM)."),
                    "code_preview": truncate(code, 800),
                },
                to_agent="validator",
            )
        ],
    }
