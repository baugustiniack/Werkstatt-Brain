"""Schonungslose Kohärenz-Prüfung: User-Prompt ↔ Referenzbilder ↔ Konzept."""

from __future__ import annotations

import logging
import re
from typing import Any

from agents.llm_client import call_llm_json, is_llm_configured
from agents.state import AgentState

logger = logging.getLogger(__name__)

_INTAKE_SYSTEM = """Du bist ein strenger Prüfingenieur für Raum-/Möbelkonzepte (CNC-Werkstatt).
Vergleiche den User-Text mit den angehängten Referenzfotos/Grundrissen.
Sei schonungslos: benenne Widersprüche, Logiklücken und fehlende Informationen klar.
Keine Höflichkeitsfloskeln, keine Spekulation als Fakt.

Antworte NUR mit JSON:
{
  "summary": string,
  "contradictions": [string, ...],
  "logic_gaps": [string, ...],
  "missing_information": [string, ...],
  "assumptions_made_by_user_or_system": [string, ...],
  "must_ask_user": [string, ...],
  "severity": "ok" | "gaps" | "blocking"
}
Regeln:
- contradictions: Text behauptet etwas, das den Bildern widerspricht (oder umgekehrt).
- logic_gaps: intern unstimmig (z.B. Maßangaben, Platzierung ohne Wandbezug).
- missing_information: nötig für ein belastbares Konzept, aber weder Text noch Bild.
- must_ask_user: konkrete Fragen (Deutsch), nur wenn blocking/gaps.
- Keine Fragen zu optionalem Freiraum/Abstand zu vorhandenem Mobiliar, wenn der Nutzer
  die Möbel-Außenmaße bereits genannt hat und Abstand nicht selbst verlangt.
- Wenn Grundriss-PDF/Plan fehlt aber behauptet wird: als missing oder contradiction markieren.
- Maximal 8 Einträge pro Liste.
"""

_CONCEPT_SYSTEM = """Du bist ein strenger Reviewer (Innenarchitektur + V&V + Werkstatt).
Prüfe den ausgearbeiteten Konzept-Contract gegen User-Prompt, Referenzbilder und frühere Findings.
Schonungslos: passt das Konzept zum realen Raum/Grundriss? Fehlen Teile? Widerspricht es den Fotos?

Antworte NUR mit JSON:
{
  "summary": string,
  "contradictions": [string, ...],
  "logic_gaps": [string, ...],
  "missing_information": [string, ...],
  "concept_issues": [string, ...],
  "must_ask_user": [string, ...],
  "verdict": "ready_for_user" | "needs_revision" | "blocking",
  "severity": "ok" | "gaps" | "blocking"
}
Regeln:
- concept_issues: konkrete Mängel am Contract/Teileliste/Maßen/Platzierung vs. Fotos.
- Referenzfotos/Grundriss vom Nutzer und erzeugte Konzept-Ansichten können multimodal beigefügt sein.
  Behaupte NICHT, dass Bilder fehlen, wenn du sie siehst oder Referenz-IDs genannt sind.
- Die Metadaten-Zeile „Konzeptbilder erzeugt: N“ zählt State-Einträge; die Bilder folgen multimodal danach.
- Frage den Nutzer NICHT, ob Referenzbilder mitgeschickt wurden – prüfe die Anhänge.
- ready_for_user = zur Nutzer-Freigabe vorlegen (auch mit gelisteten Restrisiken).
- needs_revision / blocking = schwere Probleme, Nutzer muss entscheiden/klären.
- Keine Pflichtfragen zu optionalem Abstand/Freiraum, wenn Außenmaße schon im Prompt stehen.
- Maximal 8 Einträge pro Liste. Deutsch. Keine Schönfärberei.
"""


def _empty_report(*, phase: str) -> dict[str, Any]:
    return {
        "phase": phase,
        "summary": "",
        "contradictions": [],
        "logic_gaps": [],
        "missing_information": [],
        "assumptions_made_by_user_or_system": [],
        "concept_issues": [],
        "must_ask_user": [],
        "verdict": "ready_for_user",
        "severity": "ok",
    }


def format_coherence_block(report: dict[str, Any] | None) -> str:
    if not isinstance(report, dict) or not report.get("summary") and not (
        report.get("contradictions") or report.get("logic_gaps") or report.get("missing_information")
    ):
        return ""
    lines = ["=== KOHÄRENZ-PRÜFUNG (schonungslos) ==="]
    if report.get("summary"):
        lines.append(str(report["summary"]))
    lines.append(f"Severity: {report.get('severity') or '?'} | Verdict: {report.get('verdict') or '?'}")
    for key, label in (
        ("contradictions", "Widersprüche"),
        ("logic_gaps", "Logiklücken"),
        ("missing_information", "Fehlende Informationen"),
        ("concept_issues", "Konzept-Mängel"),
        ("must_ask_user", "Muss den Nutzer klären"),
        ("assumptions_made_by_user_or_system", "Annahmen"),
    ):
        items = [str(x).strip() for x in (report.get(key) or []) if str(x).strip()]
        if items:
            lines.append(f"{label}:")
            lines.extend(f"- {x}" for x in items[:8])
    lines.append("=== Ende Kohärenz-Prüfung ===")
    return "\n".join(lines)


def _filter_spurious_findings(
    report: dict[str, Any],
    *,
    ref_ids: list[str],
    ref_images: int,
    concept_images: int,
) -> dict[str, Any]:
    """Entfernt LLM-Fehlalarme (z. B. „Bilder fehlen“, obwohl multimodal beigefügt)."""
    import copy

    out = copy.deepcopy(report)

    def _spurious(text: str) -> bool:
        t = text.lower()
        if ref_ids or ref_images > 0:
            if ref_images > 0 and any(
                p in t
                for p in (
                    "weder bilder noch",
                    "nicht mitgeschickt",
                    "keine referenzbilder",
                    "referenzfoto fehlt",
                    "bilder fehlen als",
                )
            ):
                return True
        if concept_images > 0:
            if ("konzeptbild" in t or "konzeptbilder erzeugt" in t) and any(
                p in t
                for p in (
                    "weder bilder",
                    "liefert aber weder",
                    "nicht zur prüfung",
                    "weder bilder noch beschriebene",
                )
            ):
                return True
        if "konzeptbilder erzeugt:" in t and "liefert" in t:
            return True
        return False

    for key in (
        "contradictions",
        "logic_gaps",
        "missing_information",
        "concept_issues",
        "must_ask_user",
        "assumptions_made_by_user_or_system",
    ):
        out[key] = [x for x in (out.get(key) or []) if not _spurious(str(x))]
    if not (out.get("contradictions") or out.get("must_ask_user")):
        if out.get("severity") == "blocking" and not out.get("concept_issues"):
            out["severity"] = "gaps" if out.get("logic_gaps") or out.get("missing_information") else "ok"
    return out


def analyze_intake_coherence(state: AgentState) -> dict[str, Any]:
    """Prüft Prompt + Referenzen vor/mit Requirements (ohne Concept-Contract)."""
    from agents.reference_images import (
        ensure_reference_vision_brief,
        load_reference_images_for_llm,
        reference_ground_truth_block,
    )

    brief, _ = ensure_reference_vision_brief(state)
    state_for_refs = {**state, "reference_vision_brief": brief or state.get("reference_vision_brief")}
    images = load_reference_images_for_llm(state_for_refs, limit=5)
    gt = reference_ground_truth_block(state_for_refs)
    prompt = state.get("user_prompt") or ""
    advisory = state.get("advisory_notes") or ""
    interior = state.get("interior_brief") if isinstance(state.get("interior_brief"), dict) else {}

    if not is_llm_configured():
        report = _empty_report(phase="intake")
        if not images and ("id=" in prompt or "Referenz" in prompt):
            report["missing_information"] = ["Referenzdateien erwähnt, aber keine Bilder ladbar."]
            report["severity"] = "gaps"
            report["summary"] = "Referenzen nicht als Bild prüfbar."
        return report

    user_blob = (
        f"User-Prompt:\n{prompt[:3500]}\n\n"
        f"{gt}\n\n"
        f"Vision-Brief:\n{(brief or '')[:2000]}\n\n"
        f"Interior-Brief (Kurz): { {k: interior.get(k) for k in ('room_summary', 'spatial_notes', 'need_floorplan') if interior} }\n"
        f"Advisory:\n{str(advisory)[:800]}\n"
        f"Referenzbilder multimodal: {len(images)}\n"
    )
    try:
        result = call_llm_json(
            _INTAKE_SYSTEM,
            user_blob,
            max_tokens=1800,
            images=images or None,
        )
        if not isinstance(result, dict):
            return _empty_report(phase="intake")
        result["phase"] = "intake"
        result.setdefault("verdict", "ready_for_user")
        result.setdefault("severity", "gaps" if result.get("must_ask_user") else "ok")
        ref_ids = []
        try:
            from agents.reference_images import resolve_reference_asset_ids

            ref_ids = resolve_reference_asset_ids(state_for_refs)
        except Exception:  # noqa: BLE001
            pass
        return _filter_spurious_findings(
            result, ref_ids=ref_ids, ref_images=len(images), concept_images=0
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Intake-Kohärenz fehlgeschlagen: %s", exc)
        return _empty_report(phase="intake")


def analyze_concept_coherence(state: AgentState) -> dict[str, Any]:
    """Prüft ausgearbeiteten Concept-Contract gegen Prompt + Referenzen."""
    from agents.reference_images import (
        load_review_images_for_llm,
        reference_ground_truth_block,
        resolve_reference_asset_ids,
    )

    all_images, ref_count, concept_count = load_review_images_for_llm(state, ref_limit=5, concept_limit=4)
    ref_ids = resolve_reference_asset_ids(state)
    gt = reference_ground_truth_block(state)
    contract = state.get("requirements_contract") or {}
    vv = state.get("vv_requirements") or {}
    prior = state.get("coherence_critique") if isinstance(state.get("coherence_critique"), dict) else {}
    prompt = state.get("user_prompt") or ""
    concept_meta = len(state.get("concept_image_urls") or [])

    if not is_llm_configured():
        report = _empty_report(phase="concept")
        report["summary"] = "Kein LLM – Konzept-Kritik übersprungen."
        return report

    user_blob = (
        f"User-Prompt:\n{prompt[:3000]}\n\n"
        f"{gt}\n\n"
        f"Referenz-Asset-IDs vom Nutzer: {', '.join(ref_ids) if ref_ids else '(keine)'}\n"
        f"Referenzbilder multimodal beigefügt: {ref_count}\n"
        f"Frühere Intake-Findings:\n{format_coherence_block(prior)[:1500]}\n\n"
        f"V&V-Requirements:\n{str(vv)[:2000]}\n\n"
        f"Concept-Contract:\n{str(contract)[:3500]}\n\n"
        f"Konzept-Ansichten im State: {concept_meta}\n"
        f"Konzeptbilder multimodal beigefügt: {concept_count}\n"
        f"Hinweis: Nutzer-Referenzen wurden zu Beginn mitgeschickt – nicht erneut danach fragen.\n"
    )
    try:
        result = call_llm_json(
            _CONCEPT_SYSTEM,
            user_blob,
            max_tokens=2000,
            images=all_images or None,
        )
        if not isinstance(result, dict):
            return _empty_report(phase="concept")
        result["phase"] = "concept"
        result.setdefault("verdict", "ready_for_user")
        result.setdefault("severity", "ok")
        return _filter_spurious_findings(
            result,
            ref_ids=ref_ids,
            ref_images=ref_count,
            concept_images=concept_count,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Konzept-Kohärenz fehlgeschlagen: %s", exc)
        return _empty_report(phase="concept")


def collect_clarification_questions(report: dict[str, Any] | None, *, max_questions: int = 5) -> list[str]:
    """Leitet konkrete Nutzerfragen aus der Kohärenz-Prüfung ab."""
    if not isinstance(report, dict):
        return []
    seen: set[str] = set()
    out: list[str] = []

    def _add(text: str) -> None:
        q = re.sub(r"\s+", " ", (text or "").strip())
        if not q or q.lower() in seen:
            return
        seen.add(q.lower())
        out.append(q)

    for q in report.get("must_ask_user") or []:
        _add(str(q))
        if len(out) >= max_questions:
            return out

    severity = str(report.get("severity") or "")
    verdict = str(report.get("verdict") or "")
    needs = severity in ("gaps", "blocking") or verdict in ("needs_revision", "blocking")
    if needs and len(out) < max_questions:
        for item in report.get("contradictions") or []:
            _add(f"Bitte diesen Widerspruch klären: {item}")
            if len(out) >= max_questions:
                return out
        for item in report.get("missing_information") or []:
            _add(f"Bitte fehlende Angabe ergänzen: {item}")
            if len(out) >= max_questions:
                return out
        for item in report.get("logic_gaps") or []:
            _add(f"Bitte diese Logiklücke schließen: {item}")
            if len(out) >= max_questions:
                return out
        for item in report.get("concept_issues") or []:
            _add(f"Bitte Konzept-Mangel klären: {item}")
            if len(out) >= max_questions:
                return out
    return out


def needs_user_clarification(report: dict[str, Any] | None) -> bool:
    """True, wenn vor Konzept-Freigabe noch Nutzerklärung nötig ist."""
    if not isinstance(report, dict):
        return False
    if collect_clarification_questions(report):
        return True
    return False


def unanswered_clarification_questions(
    report: dict[str, Any] | None,
    answers: list[dict[str, Any]] | None,
) -> list[str]:
    """Offene Fragen abzüglich bereits beantworteter (exakt + ähnliche)."""
    answered_exact: set[str] = set()
    answered_tokens: list[set[str]] = []
    for a in answers or []:
        if not isinstance(a, dict):
            continue
        q = re.sub(r"\s+", " ", str(a.get("question") or "").strip()).lower()
        if not q:
            continue
        answered_exact.add(q)
        toks = {t for t in re.findall(r"[a-z0-9äöüß]{4,}", q)}
        if toks:
            answered_tokens.append(toks)

    def _already_covered(q: str) -> bool:
        key = re.sub(r"\s+", " ", q).strip().lower()
        if key in answered_exact:
            return True
        toks = {t for t in re.findall(r"[a-z0-9äöüß]{4,}", key)}
        if not toks:
            return False
        for prev in answered_tokens:
            overlap = len(toks & prev) / max(len(toks), 1)
            if overlap >= 0.55:
                return True
        return False

    out: list[str] = []
    seen: set[str] = set()
    for q in collect_clarification_questions(report):
        key = re.sub(r"\s+", " ", q).strip().lower()
        if not key or key in seen or _already_covered(q):
            continue
        seen.add(key)
        out.append(q)
    return out



def merge_coherence_reports(*reports: dict[str, Any] | None) -> dict[str, Any]:
    """Führt mehrere Reports zusammen (für Escalation-Anzeige)."""
    out = _empty_report(phase="merged")
    summaries: list[str] = []
    severities = []
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        if rep.get("summary"):
            summaries.append(str(rep["summary"]))
        for key in (
            "contradictions",
            "logic_gaps",
            "missing_information",
            "assumptions_made_by_user_or_system",
            "concept_issues",
            "must_ask_user",
        ):
            for item in rep.get(key) or []:
                text = str(item).strip()
                if text and text not in out[key]:
                    out[key].append(text)
        if rep.get("severity"):
            severities.append(str(rep["severity"]))
        if rep.get("verdict"):
            out["verdict"] = str(rep["verdict"])
    out["summary"] = " | ".join(summaries)[:800]
    if "blocking" in severities:
        out["severity"] = "blocking"
    elif "gaps" in severities:
        out["severity"] = "gaps"
    return out
