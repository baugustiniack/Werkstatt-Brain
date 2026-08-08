"""Meta-Coach – autonomer Nachtarbeiter (SPEC Kap. 1.3, 2.5).

Grundstruktur für den Hintergrundprozess, der im Leerlauf (nachts oder per
On-Demand-Trigger) unerledigte `execution_logs` auswertet, Fehler
klassifiziert und daraus "Lessons Learned"-Regeldateien (z. B.
`rules_cnc_geometry.json`) pflegt, die tagsüber als System-Kontext vor den
Generierungs-Prompt gehängt werden können (Kap. 2.5).

Aktuell als heuristische Regel-Klassifizierung implementiert (kein lokales
LLM/Ollama angebunden, Kap. 1.3 Phase 2). Der `APScheduler`-Aufbau ist bereits
lauffähig, aber standardmäßig deaktiviert (`META_COACH_SCHEDULER_ENABLED`),
damit Phase-1-Entwicklungsumgebungen nicht überraschend Hintergrundjobs
starten.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db.postgres import SessionLocal
from app.models.execution_log import ExecutionLog

logger = logging.getLogger(__name__)

ErrorCategory = str

_CATEGORY_PATTERNS: dict[ErrorCategory, re.Pattern[str]] = {
    "syntax_error": re.compile(r"SyntaxError|IndentationError|NameError|TypeError|AttributeError", re.IGNORECASE),
    "geometry_conflict": re.compile(
        r"fillet|radius|chamfer|BRep_API|geometry|topolog|face boundary", re.IGNORECASE
    ),
    "tool_mismatch": re.compile(r"tool|fräser|werkzeug|diameter", re.IGNORECASE),
    "timeout": re.compile(r"timeout", re.IGNORECASE),
}


@dataclass
class MetaCoachRunSummary:
    """Ergebnis eines Meta-Coach-Durchlaufs (SPEC Kap. 2.5)."""

    evaluated_count: int = 0
    category_counts: dict[str, int] = field(default_factory=dict)
    rules_updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def classify_error_category(error_text: str | None) -> ErrorCategory:
    """Ordnet einen Fehlertext heuristisch einer Kategorie zu (SPEC Kap. 2.5).

    Erweiterungspunkt: Ersatz durch eine lokale LLM-Klassifizierung
    (Ollama/Llama-3/Qwen, Kap. 1.3 Nachtbetrieb), sobald verfügbar.
    """
    if not error_text:
        return "unknown"
    for category, pattern in _CATEGORY_PATTERNS.items():
        if pattern.search(error_text):
            return category
    return "unknown"


def _rules_file_path(category: ErrorCategory) -> Path:
    filename = "rules_cnc_geometry.json" if category == "geometry_conflict" else f"rules_{category}.json"
    return Path(settings.meta_coach_rules_dir) / filename


def load_rules(category: ErrorCategory) -> dict[str, Any]:
    path = _rules_file_path(category)
    if not path.exists():
        return {"category": category, "rules": [], "updated_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Konnte Regel-Datei %s nicht lesen: %s", path, exc)
        return {"category": category, "rules": [], "updated_at": None}


def save_rules(category: ErrorCategory, rules_doc: dict[str, Any]) -> Path:
    path = _rules_file_path(category)
    path.parent.mkdir(parents=True, exist_ok=True)
    rules_doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(rules_doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def update_rule_for_category(category: ErrorCategory, log: ExecutionLog) -> Path:
    """Fügt eine 'Lessons Learned'-Regel für eine Fehlerkategorie hinzu/aktualisiert sie."""
    rules_doc = load_rules(category)
    rules: list[dict[str, Any]] = rules_doc.setdefault("rules", [])

    error_text = log.error_traceback or log.error_message or ""
    signature = error_text.strip().splitlines()[-1] if error_text.strip() else "unbekannter Fehler"

    existing = next((r for r in rules if r.get("signature") == signature), None)
    if existing:
        existing["occurrences"] = existing.get("occurrences", 1) + 1
        existing["last_seen_session_id"] = str(log.session_id)
    else:
        rules.append(
            {
                "signature": signature,
                "category": category,
                "occurrences": 1,
                "first_seen_session_id": str(log.session_id),
                "last_seen_session_id": str(log.session_id),
                "example_prompt": log.prompt,
            }
        )

    return save_rules(category, rules_doc)


def process_agent_transcript_files(limit: int = 50) -> MetaCoachRunSummary:
    """Wertet zeitgestempelte Agent-Transcript-.txt-Dateien aus (Nutzer-Feedback:
    Log nicht in der UI, sondern als Datei für den Meta-Coach)."""
    summary = MetaCoachRunSummary()
    logs_dir = Path(settings.agent_logs_dir)
    if not logs_dir.is_dir():
        return summary

    pending = sorted(logs_dir.glob("*.txt"))[:limit]
    for path in pending:
        try:
            text = path.read_text(encoding="utf-8")
            category = classify_error_category(text)
            summary.category_counts[category] = summary.category_counts.get(category, 0) + 1

            if category not in ("unknown", "success") and (
                "SANDBOX_ERROR" in text
                or "sandbox_error" in text
                or "SyntaxError" in text
                or "Fehler" in text
            ):
                # Pseudo-ExecutionLog für update_rule_for_category
                class _FakeLog:
                    session_id = path.stem
                    error_traceback = text
                    error_message = path.name
                    prompt = None

                # Prompt aus Datei extrahieren falls vorhanden
                if "## User-Prompt" in text:
                    try:
                        _FakeLog.prompt = text.split("## User-Prompt", 1)[1].split("##", 1)[0].strip()
                    except Exception:  # noqa: BLE001
                        pass

                rules_path = update_rule_for_category(category, _FakeLog())  # type: ignore[arg-type]
                summary.rules_updated.append(str(rules_path))

            # Verarbeitet: nach .processed verschieben
            processed_dir = logs_dir / "processed"
            processed_dir.mkdir(parents=True, exist_ok=True)
            path.rename(processed_dir / path.name)
            summary.evaluated_count += 1
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"{path.name}: {exc}")

    return summary


def run_nightly_cycle(limit: int = 200) -> MetaCoachRunSummary:
    """Hauptdurchlauf des Meta-Coach (SPEC Kap. 2.5):

    1. Filtert unerledigte `execution_logs` (`evaluated_by_coach = FALSE`).
    2. Wertet Agent-Transcript-.txt-Dateien unter `agent_logs_dir` aus.
    3. Klassifiziert Fehler und pflegt Regel-Dateien.
    4. Markiert die verarbeiteten Logs als `evaluated_by_coach = TRUE`.
    """
    summary = MetaCoachRunSummary()
    db = SessionLocal()
    try:
        pending_logs = (
            db.execute(
                select(ExecutionLog)
                .where(ExecutionLog.evaluated_by_coach.is_(False))
                .order_by(ExecutionLog.created_at.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

        for log in pending_logs:
            try:
                if log.sandbox_success:
                    category = "success"
                else:
                    # Auch log_payload / Transcript-Text nutzen falls vorhanden
                    payload_text = ""
                    if isinstance(log.log_payload, dict):
                        payload_text = json.dumps(log.log_payload, ensure_ascii=False)
                    category = classify_error_category(
                        (log.error_traceback or "") + "\n" + (log.error_message or "") + "\n" + payload_text
                    )
                    if category != "unknown":
                        rules_path = update_rule_for_category(category, log)
                        summary.rules_updated.append(str(rules_path))

                summary.category_counts[category] = summary.category_counts.get(category, 0) + 1
                log.evaluated_by_coach = True
                summary.evaluated_count += 1
            except Exception as exc:  # noqa: BLE001 - ein fehlerhafter Log darf den Lauf nicht stoppen
                summary.errors.append(f"log {log.id}: {exc}")

        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("Meta-Coach-Lauf fehlgeschlagen: %s", exc)
        summary.errors.append(str(exc))
    finally:
        db.close()

    file_summary = process_agent_transcript_files(limit=limit)
    summary.evaluated_count += file_summary.evaluated_count
    summary.rules_updated.extend(file_summary.rules_updated)
    summary.errors.extend(file_summary.errors)
    for cat, count in file_summary.category_counts.items():
        summary.category_counts[cat] = summary.category_counts.get(cat, 0) + count

    logger.info(
        "Meta-Coach-Lauf abgeschlossen: %d Logs ausgewertet, Kategorien=%s, %d Regel-Updates, %d Fehler",
        summary.evaluated_count,
        summary.category_counts,
        len(summary.rules_updated),
        len(summary.errors),
    )
    return summary


_scheduler: Any = None


def start_scheduler() -> Any:
    """Startet den APScheduler-Hintergrundjob für den nächtlichen Meta-Coach-Lauf
    (SPEC Kap. 2.5: 02:00–05:00 Uhr). Stub-Charakter: wird nur aktiv, wenn
    `META_COACH_SCHEDULER_ENABLED=true` gesetzt ist (siehe `main.py` Lifespan)."""
    global _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler

    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_nightly_cycle,
        trigger="cron",
        hour=settings.meta_coach_nightly_hour,
        minute=0,
        id="meta_coach_nightly_cycle",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Meta-Coach-Scheduler gestartet (täglich %02d:00 UTC)", settings.meta_coach_nightly_hour)
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
