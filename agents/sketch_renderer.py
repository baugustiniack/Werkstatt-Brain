"""Deterministischer 2D-Konzept-Sketch-Renderer (Nutzer-Feedback zum
Chapter-3-Frontend: 'ein 2D Bild als erster Entwurf' vor der 3D-Ausarbeitung).

Erzeugt reines SVG-Markup (keine externe Abhängigkeit, keine Cloud-API) aus
der vom Concept Builder erzeugten Teile-Liste – schnell, exakt maßstäblich
und kostenlos, im Gegensatz zu einem KI-generierten Rasterbild."""

from __future__ import annotations

from html import escape
from typing import Any

_CANVAS_W = 800.0
_CANVAS_H = 520.0
_PADDING = 48.0
_AUTO_LAYOUT_GAP_MM = 60.0
_MAX_SCALE = 4.0

_RECT_FILL = "rgba(79, 157, 222, 0.16)"
_RECT_STROKE = "#4f9dde"
_TEXT_FILL = "#e5e7eb"
_MUTED_FILL = "#9aa4b2"
_TITLE_FILL = "#f8fafc"


def _part_dims(part: dict[str, Any]) -> tuple[float, float, float]:
    dims = (part.get("functional_geometry") or {}).get("dimensions_mm") or {}
    return (
        float(dims.get("x", 100.0)) or 100.0,
        float(dims.get("y", 100.0)) or 100.0,
        float(dims.get("z", 18.0)) or 18.0,
    )


def _layout_rects(parts: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Berechnet Rechtecke (x, y, w, d) je Teil in mm. Nutzt `position_xy_mm`
    für Raum-/Layout-Anfragen, sofern JEDES Teil eine Position mitbringt;
    andernfalls automatisches Zeilen-Layout, um Überlappungen zu vermeiden."""
    all_positioned = all(part.get("position_xy_mm") for part in parts)

    rects: list[dict[str, float]] = []
    if all_positioned and parts:
        for part in parts:
            w, d, _z = _part_dims(part)
            pos = part["position_xy_mm"]
            rects.append({"x": float(pos.get("x", 0)), "y": float(pos.get("y", 0)), "w": w, "d": d})
        return rects

    cursor_x = 0.0
    row_max_depth = 0.0
    cursor_y = 0.0
    max_row_width = max((_part_dims(p)[0] for p in parts), default=0.0) * 3 + _AUTO_LAYOUT_GAP_MM * 2
    for part in parts:
        w, d, _z = _part_dims(part)
        if cursor_x > 0 and cursor_x + w > max_row_width:
            cursor_x = 0.0
            cursor_y += row_max_depth + _AUTO_LAYOUT_GAP_MM
            row_max_depth = 0.0
        rects.append({"x": cursor_x, "y": cursor_y, "w": w, "d": d})
        cursor_x += w + _AUTO_LAYOUT_GAP_MM
        row_max_depth = max(row_max_depth, d)
    return rects


def render_concept_sketch_svg(project_title: str, parts: list[dict[str, Any]]) -> str:
    """Rendert einen maßstäblichen Top-Down-Entwurf aller Teile als SVG-String."""
    if not parts:
        parts = [{"name": "Werkstück"}]

    rects = _layout_rects(parts)
    min_x = min(r["x"] for r in rects)
    min_y = min(r["y"] for r in rects)
    max_x = max(r["x"] + r["w"] for r in rects)
    max_y = max(r["y"] + r["d"] for r in rects)
    bbox_w = max(max_x - min_x, 1.0)
    bbox_h = max(max_y - min_y, 1.0)

    drawable_w = _CANVAS_W - 2 * _PADDING
    drawable_h = _CANVAS_H - 2 * _PADDING - 24  # Platz für Titelzeile
    scale = min(drawable_w / bbox_w, drawable_h / bbox_h, _MAX_SCALE)

    body: list[str] = []
    for part, rect in zip(parts, rects, strict=False):
        svg_x = _PADDING + (rect["x"] - min_x) * scale
        svg_y = _PADDING + 24 + (rect["y"] - min_y) * scale
        svg_w = max(rect["w"] * scale, 4.0)
        svg_h = max(rect["d"] * scale, 4.0)

        w_mm, d_mm, z_mm = _part_dims(part)
        name = escape(str(part.get("name") or "Teil"))
        material = escape(str((part.get("material_tool_constraints") or {}).get("material_type") or ""))
        dims_label = f"{w_mm:.0f}×{d_mm:.0f}×{z_mm:.0f}mm"
        features = part.get("manufacturing_features") or []
        features_label = escape(", ".join(features)) if features else ""

        cx = svg_x + svg_w / 2
        label_y = svg_y + svg_h / 2

        body.append(
            f'<rect x="{svg_x:.1f}" y="{svg_y:.1f}" width="{svg_w:.1f}" height="{svg_h:.1f}" '
            f'fill="{_RECT_FILL}" stroke="{_RECT_STROKE}" stroke-width="1.5" rx="4" />'
        )
        body.append(
            f'<text x="{cx:.1f}" y="{label_y - 6:.1f}" text-anchor="middle" '
            f'font-size="13" font-weight="600" fill="{_TEXT_FILL}">{name}</text>'
        )
        body.append(
            f'<text x="{cx:.1f}" y="{label_y + 10:.1f}" text-anchor="middle" '
            f'font-size="11" fill="{_MUTED_FILL}">{dims_label}{" · " + material if material else ""}</text>'
        )
        if features_label:
            body.append(
                f'<text x="{cx:.1f}" y="{label_y + 24:.1f}" text-anchor="middle" '
                f'font-size="10" fill="{_MUTED_FILL}">{features_label}</text>'
            )

    title = escape(project_title or "Konzept-Entwurf")
    parts_count_label = f"{len(parts)} Teil(e) · Draufsicht, maßstäblich" if len(parts) != 1 else "Draufsicht, maßstäblich"

    svg = (
        f'<svg viewBox="0 0 {_CANVAS_W:.0f} {_CANVAS_H:.0f}" xmlns="http://www.w3.org/2000/svg" '
        f'width="100%" role="img" aria-label="{title}">'
        f'<text x="{_PADDING:.0f}" y="24" font-size="15" font-weight="700" fill="{_TITLE_FILL}">{title}</text>'
        f'<text x="{_PADDING:.0f}" y="42" font-size="11" fill="{_MUTED_FILL}">{escape(parts_count_label)}</text>'
        f"{''.join(body)}"
        f"</svg>"
    )
    return svg
