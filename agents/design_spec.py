"""Frozen Design Spec – Single Source of Truth für Maße/Türen/Fächer.

Nach Concept-V&V-Freigabe eingefroren. Nachfolgende Agenten dürfen die Spec
lesen und nur mit explizitem Nutzer-Feedback ändern. Deterministische
Plausibilitätschecks (kein LLM) fangen geometrische Widersprüche ab.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

DoorType = Literal["sliding_2", "sliding_3", "hinged", "none", "unknown"]

_VAGUE_ONLY = re.compile(
    r"^(ausreichend|egal|weiss?\s*nicht|weiß\s*nicht|irgendwie|normal|passt|"
    r"ok|okay|ja|nein|vielleicht|keine\s*ahnung|spaeter|später|"
    r"\(übersprungen\)|\(uebersprungen\)|-|\.|n/?a)$",
    re.IGNORECASE,
)
_HAS_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_MEASURE_HINT = re.compile(
    r"(mm|cm|m\b|maß|mass|höhe|hoehe|breite|tiefe|länge|laenge|decke|"
    r"spalt|abstand|raster|fach|wand|meter)",
    re.IGNORECASE,
)
_DIM_MMM = re.compile(
    r"(?P<w>\d+(?:[.,]\d+)?)\s*(?:x|×)\s*(?P<d>\d+(?:[.,]\d+)?)\s*(?:x|×)\s*(?P<h>\d+(?:[.,]\d+)?)\s*mm",
    re.IGNORECASE,
)
_DIM_M = re.compile(
    r"(?P<w>\d+(?:[.,]\d+)?)\s*(?:x|×)\s*(?P<d>\d+(?:[.,]\d+)?)\s*(?:x|×)\s*(?P<h>\d+(?:[.,]\d+)?)\s*m\b",
    re.IGNORECASE,
)
_SINGLE_MM = re.compile(
    r"(?P<val>\d+(?:[.,]\d+)?)\s*(?P<unit>mm|cm|m)\b",
    re.IGNORECASE,
)


def _f(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _to_mm(val: float, unit: str) -> float:
    u = unit.lower()
    if u == "m":
        return val * 1000.0
    if u == "cm":
        return val * 10.0
    return val


class OuterDimensionsMm(BaseModel):
    width: float = Field(..., gt=0, description="Außenbreite mm (X)")
    depth: float = Field(..., gt=0, description="Außentiefe mm (Y)")
    height: float = Field(..., gt=0, description="Außenhöhe mm (Z)")


class DesignSpec(BaseModel):
    """Unveränderliche Produkt-Spec nach Concept-V&V (sofern frozen=True)."""

    project_title: str = "Werkstatt-Projekt"
    outer_mm: OuterDimensionsMm | None = None
    door_type: DoorType = "unknown"
    door_count: int = Field(default=0, ge=0, le=8)
    compartments: int = Field(default=1, ge=1, le=12)
    shelf_pitch_mm: float = Field(default=32.0, gt=0)
    plate_thickness_mm: float = Field(default=19.0, gt=0)
    material: str = "Holzwerkstoff"
    ceiling_height_mm: float | None = None
    clearance_above_mm: float | None = None
    sliding_overlap_mm: float = Field(default=40.0, ge=0)
    max_shelf_span_mm: float = Field(
        default=800.0,
        gt=0,
        description="Max. Spannweite Einlegeboden ohne Mittelwand (19mm Span, Ordner)",
    )
    frozen: bool = False
    frozen_at: str | None = None
    source_notes: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)

    @field_validator("material", mode="before")
    @classmethod
    def _strip_material(cls, v: Any) -> str:
        return str(v or "Holzwerkstoff").strip() or "Holzwerkstoff"

    def bay_width_mm(self) -> float | None:
        if not self.outer_mm or self.compartments < 1:
            return None
        # grob: Innenbreite ≈ Außenbreite − 2×Seitenwand
        inner = max(0.0, self.outer_mm.width - 2 * self.plate_thickness_mm)
        return inner / self.compartments

    def as_prompt_block(self) -> str:
        lines = [
            "=== FROZEN DESIGN SPEC (verbindlich, nicht widersprechen) ===",
            f"Titel: {self.project_title}",
            f"frozen={self.frozen}",
        ]
        if self.outer_mm:
            o = self.outer_mm
            lines.append(
                f"Außenmaß: {o.width:.0f} × {o.depth:.0f} × {o.height:.0f} mm (B×T×H)"
            )
        lines.extend(
            [
                f"Türen: {self.door_type} (count={self.door_count})",
                f"Fächer/Kompartimente: {self.compartments}",
                f"Einlegeboden-Raster: {self.shelf_pitch_mm:g} mm",
                f"Plattenstärke: {self.plate_thickness_mm:g} mm",
                f"Material: {self.material}",
            ]
        )
        if self.ceiling_height_mm:
            lines.append(f"Deckenhöhe: {self.ceiling_height_mm:.0f} mm")
        if self.clearance_above_mm is not None:
            lines.append(f"Spalt oben: {self.clearance_above_mm:.0f} mm")
        bay = self.bay_width_mm()
        if bay is not None:
            lines.append(f"Max. Fachbreite (rechnerisch): {bay:.0f} mm")
        if self.source_notes:
            lines.append("Herkunft: " + "; ".join(self.source_notes[:6]))
        lines.append("=== ENDE DESIGN SPEC ===")
        return "\n".join(lines)

    def to_state_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def load_design_spec(raw: Any) -> DesignSpec | None:
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return DesignSpec.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DesignSpec ungültig: %s", exc)
        return None


def is_vague_answer(question: str, answer: str) -> bool:
    """True wenn die Antwort für eine Maß-/Mengenfrage zu ungenau ist."""
    a = (answer or "").strip()
    if not a:
        return True
    if _VAGUE_ONLY.match(a):
        return True
    q = question or ""
    if _MEASURE_HINT.search(q) and not _HAS_NUMBER.search(a):
        return True
    return False


def vague_answer_retry_hint(question: str) -> str:
    return (
        f"Bitte präziser antworten (Zahl mit Einheit, z.B. mm/cm/m). "
        f"Offene Frage: {question}"
    )


def _parse_outer_from_text(text: str) -> OuterDimensionsMm | None:
    m = _DIM_MMM.search(text or "")
    if m:
        w, d, h = _f(m.group("w")), _f(m.group("d")), _f(m.group("h"))
        if w and d and h and min(w, d, h) > 0:
            return OuterDimensionsMm(width=w, depth=d, height=h)
    m = _DIM_M.search(text or "")
    if m:
        w, d, h = _f(m.group("w")), _f(m.group("d")), _f(m.group("h"))
        if w and d and h and min(w, d, h) > 0:
            return OuterDimensionsMm(width=w * 1000, depth=d * 1000, height=h * 1000)
    return None


def _extract_mm_near_keywords(text: str, keywords: tuple[str, ...]) -> float | None:
    low = (text or "").lower()
    for kw in keywords:
        idx = low.find(kw)
        if idx < 0:
            continue
        window = text[max(0, idx - 40) : idx + 80]
        m = _SINGLE_MM.search(window)
        if m:
            return _to_mm(_f(m.group("val")) or 0, m.group("unit"))
    return None


def _infer_door_type(text: str) -> tuple[DoorType, int]:
    low = (text or "").lower()
    if "schiebetür" in low or "schiebetuer" in low or "sliding" in low:
        if "drei" in low or "3-flügel" in low or "3 fluegel" in low:
            return "sliding_3", 3
        return "sliding_2", 2
    if "drehtür" in low or "drehtuer" in low or "anschlag" in low:
        return "hinged", 2
    if "ohne tür" in low or "offen" in low and "schrank" in low:
        return "none", 0
    return "unknown", 0


def _infer_compartments(text: str, door_type: DoorType, door_count: int) -> int:
    low = (text or "").lower()
    m = re.search(r"(\d+)\s*(?:fächer|faecher|kompartiment|felder|spalten)", low)
    if m:
        return max(1, min(12, int(m.group(1))))
    if door_type.startswith("sliding") and door_count >= 2:
        return door_count
    if "mittelwand" in low or "trennwand" in low:
        return 2
    return 1


def _extract_mm_from_qa(answers: list[dict[str, Any]], question_hints: tuple[str, ...]) -> float | None:
    """Zahl aus Q&A, wenn die Frage einen der Hinweise enthält."""
    for item in answers:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or "").lower()
        if not any(h in q for h in question_hints):
            continue
        a = str(item.get("answer") or "")
        m = _SINGLE_MM.search(a)
        if m:
            return _to_mm(_f(m.group("val")) or 0, m.group("unit"))
        # reine Zahl ohne Einheit → mm wenn groß, sonst cm-Heuristik
        m2 = _HAS_NUMBER.search(a)
        if m2:
            val = _f(m2.group(0))
            if val is None:
                continue
            if val < 30:
                return val * 1000.0  # 2.4 → unwahrscheinlich; eher 2,40 m schon mit Unit
            if val <= 400:
                return val * 10.0  # 240 cm
            return val  # 2400 mm
    return None


def build_design_spec_from_state(state: dict[str, Any]) -> DesignSpec:
    """Extrahiert Spec aus Prompt, V&V und Q&A (noch nicht zwingend frozen)."""
    prompt = str(state.get("user_prompt") or "")
    vv = state.get("vv_requirements") if isinstance(state.get("vv_requirements"), dict) else {}
    title = str(vv.get("title") or "").strip() or "Werkstatt-Projekt"
    notes: list[str] = []

    qa_list: list[dict[str, Any]] = []
    for key in ("vv_qa_answers", "concept_qa_answers"):
        for item in state.get(key) or []:
            if isinstance(item, dict):
                qa_list.append(item)

    blobs = [prompt]
    for req in vv.get("requirements") or []:
        if isinstance(req, dict):
            blobs.append(str(req.get("text") or ""))
    for qa in qa_list:
        blobs.append(f"{qa.get('question')}: {qa.get('answer')}")
    blob = "\n".join(blobs)

    outer = _parse_outer_from_text(blob)
    if outer:
        notes.append("Außenmaß aus Text/Q&A")
    else:
        w = _extract_mm_near_keywords(blob, ("breite", "width"))
        d = _extract_mm_near_keywords(blob, ("tiefe", "depth"))
        h = _extract_mm_near_keywords(blob, ("gesamthöhe", "gesamthoehe", "schrankhöhe", "schrankhoehe"))
        if w and d and h:
            outer = OuterDimensionsMm(width=w, depth=d, height=h)
            notes.append("Außenmaß aus Einzelangaben")

    door_type, door_count = _infer_door_type(blob)
    compartments = _infer_compartments(blob, door_type, door_count)
    if outer and outer.width >= 1800 and compartments < 3:
        compartments = 3
        notes.append("Mind. 3 Fächer wegen Spannweite bei Breite ≥1800 mm")

    ceiling = _extract_mm_from_qa(qa_list, ("decke", "deckenhöhe", "deckenhoehe", "raumhöhe", "raumhoehe"))
    if ceiling is None:
        ceiling = _extract_mm_near_keywords(blob, ("deckenhöhe", "deckenhoehe", "raumhöhe"))
    clearance = _extract_mm_from_qa(qa_list, ("spalt", "luft nach oben", "freiraum oben", "restspalt"))
    if clearance is None:
        clearance = _extract_mm_near_keywords(blob, ("spalt oben", "restspalt", "freiraum oben"))
    if ceiling and outer and clearance is None:
        clearance = max(0.0, ceiling - outer.height)
        notes.append("Spalt oben = Decke − Schrankhöhe")

    material = "Holzwerkstoff"
    for token in ("eiche", "buche", "multiplex", "mdf", "spanplatte", "birke"):
        if token in blob.lower():
            material = token.capitalize()
            break

    pitch = 32.0
    if re.search(r"\b32\b", blob) and ("loch" in blob.lower() or "raster" in blob.lower()):
        pitch = 32.0
        notes.append("32er-Raster erkannt")

    thickness = _extract_mm_near_keywords(blob, ("plattenstärke", "plattenstaerke", "plattenstarke"))
    if thickness is None:
        thickness = 19.0

    return DesignSpec(
        project_title=title[:120],
        outer_mm=outer,
        door_type=door_type,
        door_count=door_count,
        compartments=compartments,
        shelf_pitch_mm=pitch,
        plate_thickness_mm=thickness,
        material=material,
        ceiling_height_mm=ceiling,
        clearance_above_mm=clearance,
        frozen=False,
        source_notes=notes,
    )


def freeze_design_spec(spec: DesignSpec) -> DesignSpec:
    data = spec.model_copy(deep=True)
    data.frozen = True
    data.frozen_at = datetime.now(timezone.utc).isoformat()
    return data


def validate_design_spec(spec: DesignSpec) -> DesignSpec:
    """Deterministische Geometrie-/Statik-Plausibilität (kein LLM)."""
    errors: list[str] = []
    warnings: list[str] = []
    s = spec.model_copy(deep=True)

    if s.outer_mm is None:
        errors.append("Außenmaß (B×T×H) fehlt – Design Spec unvollständig.")
    else:
        o = s.outer_mm
        if o.width < 200 or o.depth < 150 or o.height < 300:
            errors.append(
                f"Außenmaß {o.width:.0f}×{o.depth:.0f}×{o.height:.0f} mm wirkt unrealistisch klein."
            )
        if o.width > 4000 or o.height > 3000:
            warnings.append(
                f"Außenmaß {o.width:.0f}×{o.height:.0f} mm sehr groß – Transport/Deckenhöhe prüfen."
            )

        if s.ceiling_height_mm is not None:
            top = o.height + (s.clearance_above_mm or 0)
            if top > s.ceiling_height_mm + 1:
                errors.append(
                    f"Schrankhöhe {o.height:.0f} mm"
                    + (f" + Spalt {s.clearance_above_mm:.0f} mm" if s.clearance_above_mm else "")
                    + f" überschreitet Deckenhöhe {s.ceiling_height_mm:.0f} mm."
                )
            elif s.clearance_above_mm is not None and s.clearance_above_mm < 10:
                warnings.append(
                    f"Nur {s.clearance_above_mm:.0f} mm Spalt zur Decke – Schiebetürlauf/Lüftung eng."
                )

        bay = s.bay_width_mm()
        if bay is not None and bay > s.max_shelf_span_mm:
            errors.append(
                f"Fachbreite ≈ {bay:.0f} mm > {s.max_shelf_span_mm:.0f} mm "
                f"(Durchbiegungsrisiko bei {s.plate_thickness_mm:g} mm Platte / Ordnerlast). "
                f"Mittelwand einplanen oder compartments erhöhen (aktuell {s.compartments})."
            )

        if s.door_type == "sliding_2" and s.door_count >= 2:
            leaf = o.width / 2
            access = leaf - s.sliding_overlap_mm
            if access < 350:
                warnings.append(
                    f"Schiebetür 2-flügelig: nutzbare Öffnung ≈ {access:.0f} mm "
                    f"(Flügel {leaf:.0f} mm − Überlappung {s.sliding_overlap_mm:.0f} mm) – "
                    "Ordnerzugriff in der Mitte eingeschränkt."
                )
            if s.compartments < 2 and o.width >= 1200:
                warnings.append(
                    "2-m-Schiebetürschrank ohne Mittelwand: Fachspannweite und Türüberdeckung prüfen."
                )

    if s.shelf_pitch_mm not in (32.0, 64.0) and abs(s.shelf_pitch_mm - 32) > 0.1:
        warnings.append(f"Unübliches Lochreihen-Raster {s.shelf_pitch_mm:g} mm (üblich 32).")

    if s.plate_thickness_mm not in (12, 15, 16, 18, 19, 22, 25, 30):
        warnings.append(f"Unübliche Plattenstärke {s.plate_thickness_mm:g} mm.")

    s.validation_errors = errors
    s.validation_warnings = warnings
    return s


def apply_spec_to_contract_parts(
    contract: dict[str, Any],
    spec: DesignSpec,
) -> dict[str, Any]:
    """Zwingt Außenmaß der Spec auf das Hauptteil (erstes/größtes), ohne Spec zu brechen."""
    out = dict(contract)
    parts = list(out.get("parts") or [])
    if not parts or not spec.outer_mm:
        return out
    # Hauptkorpus: größtes Volumen oder Name mit Schrank/Korpus
    idx = 0
    best = -1.0
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").lower()
        geom = p.get("functional_geometry") if isinstance(p.get("functional_geometry"), dict) else {}
        dims = geom.get("dimensions_mm") if isinstance(geom.get("dimensions_mm"), dict) else {}
        vol = float(dims.get("x") or 0) * float(dims.get("y") or 0) * float(dims.get("z") or 0)
        score = vol
        if any(k in name for k in ("schrank", "korpus", "cabinet", "büro", "buero")):
            score += 1e12
        if score > best:
            best = score
            idx = i
    main = dict(parts[idx]) if isinstance(parts[idx], dict) else {"name": "Korpus"}
    geom = dict(main.get("functional_geometry") or {})
    geom["dimensions_mm"] = {
        "x": spec.outer_mm.width,
        "y": spec.outer_mm.depth,
        "z": spec.outer_mm.height,
    }
    main["functional_geometry"] = geom
    mats = dict(main.get("material_tool_constraints") or {})
    mats["plate_thickness_mm"] = spec.plate_thickness_mm
    if spec.material:
        mats.setdefault("material_type", spec.material)
    main["material_tool_constraints"] = mats
    gaps = list(main.get("gap_analysis") or [])
    gaps.append(
        f"Außenmaß an Frozen Design Spec gebunden: "
        f"{spec.outer_mm.width:.0f}×{spec.outer_mm.depth:.0f}×{spec.outer_mm.height:.0f} mm."
    )
    main["gap_analysis"] = gaps
    parts[idx] = main
    out["parts"] = parts
    if spec.project_title:
        out["project_title"] = spec.project_title
    return out
