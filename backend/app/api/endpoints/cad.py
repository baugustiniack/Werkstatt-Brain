"""REST- & WebSocket-Endpunkte für die CAD-Generierung via LangGraph-Agenten-
Workflow (SPEC Kap. 3, 5.3).

- `POST /generate` – registriert eine neue Session (Default) oder führt den
  Workflow synchron aus (`blocking=True`, für Skripte/Tests/nicht-UI-Aufrufer).
- `WS /stream/{session_id}` – führt eine registrierte Session aus und streamt
  jeden LangGraph-Node-Übergang live an den Client (Kap. 5.3 Zeile
  `/api/v1/cad/stream/{session_id}`). Behandelt `interrupt()`-Eskalationen
  (Kap. 3.5) inline: sendet ein `escalation`-Event und wartet auf die
  Nutzerentscheidung über dieselbe Verbindung, bevor der Graph fortgesetzt wird.
- `GET /download/{session_id}/{kind}` – liefert die von der Sandbox exportierte
  STEP-/STL-Datei einer abgeschlossenen Session aus.
- `POST /resume` – legacy REST-Fortsetzung eines pausierten Workflows (für
  nicht-UI-Aufrufer ohne WebSocket).
"""

import asyncio
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from agents.graph import WORKFLOW
from app.config import settings
from app.services import paused_run_store
from app.services.system_awake import keep_system_awake

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cad", tags=["cad"])

# Sessions, die per POST /generate registriert, aber noch nicht ausgeführt
# wurden. Der eigentliche Graph-Lauf wird erst gestartet, wenn der Client die
# WS-Verbindung unter /stream/{session_id} öffnet. Bewusst simpler In-Memory-
# Speicher (Single-Prozess-Dev-Setup); für Multi-Worker-Deployments müsste
# dies durch Redis ersetzt werden.
PENDING_SESSIONS: dict[str, dict[str, Any]] = {}
# Cooperative Cancel: Session-IDs, die der Client abgebrochen hat. Der
# WS-Stream prüft das Flag zwischen Chunks und beendet dann sauber.
CANCELLED_SESSIONS: set[str] = set()
# Mapping CAD-Session → Conversation für Artefakt-Persistenz (auch disk-gestützt)
SESSION_CONVERSATIONS: dict[str, str] = {}
# Pausierte Runs (nach Abbrechen / Disconnect): Snapshot + Disk-Spiegel
PAUSED_RUNS: dict[str, dict[str, Any]] = {}


def _session_conversation_map_path() -> Path:
    return Path(settings.conversations_dir) / "_session_conversations.json"


def _bind_session_conversation(session_id: str, conversation_id: str | None) -> None:
    """Merkt Session→Conversation in RAM + JSON (überlebt API-Restart)."""
    if not conversation_id:
        return
    SESSION_CONVERSATIONS[session_id] = conversation_id
    try:
        path = _session_conversation_map_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = {}
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(raw, dict):
                data = {str(k): str(v) for k, v in raw.items() if v}
        data[session_id] = conversation_id
        if len(data) > 800:
            data = dict(list(data.items())[-500:])
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Session→Conversation-Map konnte nicht geschrieben werden: %s", exc)


def _resolve_conversation_id(session_id: str, values: dict[str, Any] | None = None) -> str | None:
    cid = SESSION_CONVERSATIONS.get(session_id)
    if cid:
        return cid
    if values:
        raw = values.get("conversation_id")
        if raw:
            cid = str(raw)
            SESSION_CONVERSATIONS[session_id] = cid
            return cid
    try:
        path = _session_conversation_map_path()
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(raw, dict) and session_id in raw:
                cid = str(raw[session_id])
                SESSION_CONVERSATIONS[session_id] = cid
                return cid
    except Exception as exc:  # noqa: BLE001
        logger.warning("Session→Conversation-Map konnte nicht gelesen werden: %s", exc)
    return None


class CadGenerateRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=3,
        max_length=50_000,
        description="Freitext-Beschreibung des gewünschten Werkstücks (inkl. Referenzen, max. 50k)",
    )
    session_id: str | None = Field(
        None, description="Optionale Session-ID; ohne Angabe wird eine neue Session erzeugt."
    )
    conversation_id: str | None = Field(
        None, description="Optionale Chat-Unterhaltung (Gemini-ähnlich); Artefakte werden dort gespeichert."
    )
    blocking: bool = Field(
        False,
        description=(
            "Wenn True: führt den kompletten Workflow synchron aus und gibt das Endergebnis "
            "direkt zurück (praktisch für Skripte/Tests). Wenn False (Default): registriert nur "
            "die Session; der Live-Fortschritt wird über WS /cad/stream/{session_id} verfolgt."
        ),
    )
    persist_user_message: bool = Field(
        True,
        description=(
            "Wenn False: User-Nachricht wurde bereits über /conversations/.../messages gespeichert "
            "(vermeidet Duplikate)."
        ),
    )


class CadPendingResponse(BaseModel):
    session_id: str
    status: str = "pending"


class CadResumeRequest(BaseModel):
    session_id: str = Field(..., description="Session-ID eines pausierten Workflows (aus /generate)")
    decision: Any = Field(..., description="User-Entscheidung zur Fortsetzung nach einer Eskalation")


class CadResumeRunRequest(BaseModel):
    session_id: str = Field(..., description="Abgebrochene CAD-Session, die fortgesetzt werden soll")
    conversation_id: str | None = Field(None, description="Optional: Chat-Unterhaltung zur Verknüpfung")


class CadResumeRunResponse(BaseModel):
    session_id: str
    resumed_from: str
    status: str = "pending"
    concept_approved: bool = False
    has_concept: bool = False
    completed_parts: list[dict[str, Any]] | None = None
    current_part_index: int = 0
    total_parts: int = 0
    concept_image_url: str | None = None
    requirements_contract: dict[str, Any] | None = None


class CadWorkflowResponse(BaseModel):
    session_id: str
    status: str  # "completed" | "failed" | "human_approval_required"
    generated_code: str | None = None
    sandbox_result: dict[str, Any] | None = None
    requirements_contract: dict[str, Any] | None = None
    stock_and_tool_context: dict[str, Any] | None = None
    escalation: dict[str, Any] | None = None
    iteration_count: int = 0
    concept_sketch_svg: str | None = None
    concept_image_url: str | None = None
    completed_parts: list[dict[str, Any]] | None = None
    current_part_index: int = 0
    total_parts: int = 0
    cancelled: bool = False
    agent_transcript: list[dict[str, Any]] | None = None
    montage_result: dict[str, Any] | None = None
    assembly_plan: dict[str, Any] | None = None
    assembly_manual: dict[str, Any] | None = None


def _assistant_summary(values: dict[str, Any], status: str) -> str:
    """Nutzer-sichtbare Zusammenfassung inkl. wesentlicher Auftragsdetails."""
    contract = values.get("requirements_contract") or {}
    title = contract.get("project_title") or "Auftrag"
    parts = contract.get("parts") or []
    completed = values.get("completed_parts") or []
    lines: list[str] = []

    if status == "human_approval_required":
        lines.append(f"Konzept „{title}“ liegt zur Freigabe bereit ({len(parts)} Teil(e)).")
    elif status == "completed":
        names = ", ".join((p.get("name") or f"Teil {i+1}") for i, p in enumerate(completed)) or "keine Teile"
        lines.append(f"Ausarbeitung abgeschlossen: {title} · {len(completed)} Teil(e): {names}.")
    elif status == "cancelled":
        if completed:
            names = ", ".join((p.get("name") or f"Teil {i+1}") for i, p in enumerate(completed))
            lines.append(
                f"Workflow pausiert – {len(completed)} fertige(s) Teil(e) bleiben erhalten "
                f"({names}). Mit „Fortsetzen“ weitermachen."
            )
        else:
            lines.append("Workflow pausiert. Mit „Fortsetzen“ weitermachen.")
    elif status == "failed":
        err = values.get("error") or values.get("sandbox_error") or ""
        lines.append(f"Ausarbeitung fehlgeschlagen ({title}).")
        if err:
            lines.append(str(err)[:800])
    else:
        lines.append(f"Status: {status} ({title}).")

    if parts:
        part_lines = []
        for i, p in enumerate(parts[:12]):
            name = p.get("name") or f"Teil {i + 1}"
            dims = p.get("dimensions_mm") or p.get("size_mm") or ""
            part_lines.append(f"- {name}" + (f" ({dims})" if dims else ""))
        lines.append("Geplante Teile:\n" + "\n".join(part_lines))

    overview = (contract.get("overview") or contract.get("summary") or contract.get("description") or "").strip()
    if overview:
        lines.append("Kurzbeschreibung:\n" + overview[:2000])

    montage = values.get("montage_result") or {}
    if values.get("montage_assessed"):
        ok = montage.get("assemblable")
        tools_rec = montage.get("tools_recommended") or []
        note = "Montage: " + ("OK" if ok else "kritische Passung")
        if tools_rec:
            note += f" · {len(tools_rec)} Werkzeug-Empfehlung(en)"
        lines.append(note)

    manual = values.get("assembly_manual") or {}
    md = (
        manual.get("markdown")
        or manual.get("manual_markdown")
        or (values.get("assembly_plan") or {}).get("manual_markdown")
        or ""
    )
    if isinstance(md, str) and md.strip():
        lines.append("Montageanleitung (Auszug):\n" + md.strip()[:4000])

    return "\n\n".join(lines)



def _persist_session_transcript(session_id: str, values: dict[str, Any], *, status: str) -> None:
    """Speichert Transcript als .txt (Meta-Coach), in execution_logs, und hängt
    Konzeptfoto/3D-Exports an die Conversation an."""
    from app.services.agent_log_writer import write_agent_transcript_txt

    transcript = values.get("agent_transcript") or []
    txt_path = write_agent_transcript_txt(
        session_id,
        transcript,
        user_prompt=values.get("user_prompt"),
        status=status,
    )

    try:
        from app.db.postgres import SessionLocal
        from app.models.execution_log import ExecutionLog

        db = SessionLocal()
        try:
            try:
                session_uuid = uuid.UUID(session_id)
            except ValueError:
                session_uuid = uuid.uuid4()
            db.add(
                ExecutionLog(
                    session_id=session_uuid,
                    prompt=values.get("user_prompt"),
                    generated_code=values.get("generated_code"),
                    sandbox_success=status == "completed",
                    log_payload={
                        "kind": "agent_session_transcript",
                        "status": status,
                        "agent_transcript": transcript,
                        "transcript_txt": str(txt_path) if txt_path else None,
                        "requirements_contract": values.get("requirements_contract"),
                        "completed_parts_count": len(values.get("completed_parts") or []),
                        "concept_image_url": values.get("concept_image_url"),
                    },
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Konnte Agent-Transcript nicht in DB speichern: %s", exc)

    conversation_id = _resolve_conversation_id(session_id, values)
    if conversation_id and status in ("completed", "failed", "cancelled", "human_approval_required"):
        try:
            from app.db.postgres import SessionLocal
            from app.services import conversation_store

            db = SessionLocal()
            try:
                conv_uuid = uuid.UUID(conversation_id)
                # Bei Konzept-Freigabe schon Foto speichern; bei completed auch Parts
                conversation_store.persist_session_artifacts(db, conv_uuid, session_id, values)
                # Konzept-Foto automatisch in Inventar-DB (Tag: KI-Generiert) + Chat-Link
                try:
                    from app.services.concept_inventory import auto_persist_concept_inventory

                    auto_persist_concept_inventory(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        values=values,
                    )
                except Exception as inv_exc:  # noqa: BLE001
                    logger.warning("Konzept→Inventar fehlgeschlagen: %s", inv_exc)
                # KI-generierte STEP/STL aller Teile → Inventar + Chat-Verknüpfung
                try:
                    from app.services.cad_model_inventory import auto_persist_cad_models_inventory

                    auto_persist_cad_models_inventory(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        values=values,
                    )
                except Exception as model_exc:  # noqa: BLE001
                    logger.warning("3D-Modelle→Inventar fehlgeschlagen: %s", model_exc)
                conversation_store.add_message(
                    db,
                    conv_uuid,
                    "assistant",
                    _assistant_summary(values, status),
                    cad_session_id=session_id,
                    meta={"status": status},
                )
                # Transcript immer anhängen (nie ersetzen/löschen)
                if txt_path and txt_path.is_file():
                    from app.models.conversation import ConversationArtifact
                    import shutil
                    from pathlib import Path
                    from datetime import datetime, timezone

                    dest_dir = Path(settings.conversations_dir) / conversation_id
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                    dest = dest_dir / f"{txt_path.stem}_{stamp}{txt_path.suffix}"
                    shutil.copy2(txt_path, dest)
                    db.add(
                        ConversationArtifact(
                            conversation_id=conv_uuid,
                            cad_session_id=session_id,
                            kind="transcript",
                            file_path=str(dest),
                            label=dest.name,
                            meta={"status": status, "session_id": session_id},
                        )
                    )
                    db.commit()
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Artefakt-Persistenz fehlgeschlagen: %s", exc)


def _build_response(session_id: str, result: dict[str, Any], *, cancelled: bool = False) -> CadWorkflowResponse:
    contract = result.get("requirements_contract") or {}
    total_parts = len(contract.get("parts", []))
    completed_parts = result.get("completed_parts") or []
    current_part_index = result.get("current_part_index", 0)

    if cancelled:
        return CadWorkflowResponse(
            session_id=session_id,
            status="failed",
            cancelled=True,
            requirements_contract=contract or None,
            concept_sketch_svg=result.get("concept_sketch_svg"),
            concept_image_url=result.get("concept_image_url"),
            completed_parts=completed_parts or None,
            current_part_index=current_part_index,
            total_parts=total_parts,
            iteration_count=result.get("iteration_count", 0),
            agent_transcript=result.get("agent_transcript") or None,
        )

    interrupts = result.get("__interrupt__")
    if interrupts:
        interrupt_obj = interrupts[0]
        interrupt_payload = getattr(interrupt_obj, "value", interrupt_obj)
        return CadWorkflowResponse(
            session_id=session_id,
            status="human_approval_required",
            requirements_contract=contract or None,
            stock_and_tool_context=result.get("stock_and_tool_context"),
            escalation=interrupt_payload,
            iteration_count=result.get("iteration_count", 0),
            concept_sketch_svg=result.get("concept_sketch_svg"),
            concept_image_url=result.get("concept_image_url"),
            completed_parts=completed_parts or None,
            current_part_index=current_part_index,
            total_parts=total_parts,
            agent_transcript=result.get("agent_transcript") or None,
        )

    # Ein Teil gilt erst als "completed", nachdem der Supervisor es in
    # completed_parts gesichert und current_part_index erhöht hat (siehe
    # agents/nodes/supervisor.py); sandbox_result wird dabei zurückgesetzt,
    # daher entscheidet der Fortschritt über die Teile-Liste, nicht mehr
    # allein `sandbox_result.status`.
    is_fully_done = (
        total_parts > 0
        and current_part_index >= total_parts
        and bool(result.get("montage_assessed"))
    )

    return CadWorkflowResponse(
        session_id=session_id,
        status="completed" if is_fully_done else "failed",
        generated_code=result.get("generated_code"),
        sandbox_result=result.get("sandbox_result") or None,
        requirements_contract=contract or None,
        stock_and_tool_context=result.get("stock_and_tool_context"),
        iteration_count=result.get("iteration_count", 0),
        concept_sketch_svg=result.get("concept_sketch_svg"),
        concept_image_url=result.get("concept_image_url"),
        completed_parts=completed_parts or None,
        current_part_index=current_part_index,
        total_parts=total_parts,
        agent_transcript=result.get("agent_transcript") or None,
        montage_result=result.get("montage_result") or None,
        assembly_plan=result.get("assembly_plan") or None,
        assembly_manual=result.get("assembly_manual") or None,
    )


@router.post("/generate")
async def generate_cad(request: CadGenerateRequest) -> CadWorkflowResponse | CadPendingResponse:
    """Startet eine neue CAD-Session. Standardmäßig nicht-blockierend: das
    Frontend registriert den Prompt und öffnet danach die Live-Stream-WS-
    Verbindung (SPEC Kap. 5.3)."""
    session_id = request.session_id or str(uuid.uuid4())

    # Bei fortlaufender Unterhaltung: bisherigen Chat-Kontext dem Prompt voranstellen.
    prompt = request.prompt
    if request.conversation_id:
        _bind_session_conversation(session_id, request.conversation_id)
        from app.db.postgres import SessionLocal
        from app.services import conversation_store

        db = SessionLocal()
        try:
            conv_uuid = uuid.UUID(request.conversation_id)
            conv = conversation_store.get_conversation(db, conv_uuid)
            if conv is None:
                raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden.")
            if conv.messages:
                prior = []
                for m in conv.messages[-8:]:
                    content = (m.content or "").strip()
                    if not content:
                        continue
                    # Alte Referenz-/Vision-Blöcke kürzen – verhindert Prompt-Explosion
                    if len(content) > 2500:
                        content = content[:2500] + "…"
                    prior.append(f"{m.role.upper()}: {content}")
                if prior:
                    prompt = (
                        "Bisheriger Unterhaltungskontext:\n"
                        + "\n".join(prior)
                        + "\n\nNeue Nutzeranweisung:\n"
                        + request.prompt
                    )
                if len(prompt) > 50_000:
                    prompt = prompt[-50_000:]
            if request.persist_user_message:
                try:
                    conversation_store.add_message(
                        db,
                        conv_uuid,
                        "user",
                        request.prompt.strip(),
                        cad_session_id=session_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("User-Nachricht konnte nicht gespeichert werden")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Chat-Nachricht konnte nicht gespeichert werden: {exc}",
                    ) from exc
        finally:
            db.close()

    initial_state = {
        "user_prompt": prompt,
        "session_id": session_id,
        "conversation_id": request.conversation_id,
        "iteration_count": 0,
        "human_approval_required": False,
        "messages": [{"role": "user", "content": prompt}],
    }

    if request.blocking:
        config = {"configurable": {"thread_id": session_id}}
        logger.info("CAD-Workflow gestartet (blocking, session_id=%s)", session_id)
        with keep_system_awake(f"cad-blocking:{session_id}"):
            result = WORKFLOW.invoke(initial_state, config=config)
        resp = _build_response(session_id, result)
        _persist_session_transcript(session_id, result, status=resp.status)
        return resp

    PENDING_SESSIONS[session_id] = initial_state
    CANCELLED_SESSIONS.discard(session_id)
    logger.info("CAD-Session registriert (session_id=%s) – erwarte WS-Verbindung auf /stream", session_id)
    return CadPendingResponse(session_id=session_id)


@router.post("/resume", response_model=CadWorkflowResponse)
async def resume_cad(request: CadResumeRequest) -> CadWorkflowResponse:
    """Setzt einen per `interrupt()` pausierten Workflow über REST fort
    (Legacy-Pfad für nicht-UI-Aufrufer; das Frontend nutzt stattdessen die
    Eskalations-Antwort direkt auf der WS-Verbindung)."""
    config = {"configurable": {"thread_id": request.session_id}}

    logger.info("CAD-Workflow fortgesetzt (session_id=%s)", request.session_id)
    with keep_system_awake(f"cad-resume:{request.session_id}"):
        result = WORKFLOW.invoke(Command(resume=request.decision), config=config)

    return _build_response(request.session_id, result)


# ─────────────────────────────────────────────────────────────────────────────
# WS /api/v1/cad/stream/{session_id} – Live-Node-Updates (SPEC Kap. 5.2, 5.3)
# ─────────────────────────────────────────────────────────────────────────────


def _run_stream_in_thread(
    graph_input: Any,
    config: dict[str, Any],
    loop: asyncio.AbstractEventLoop,
    queue: "asyncio.Queue[tuple[str, Any]]",
) -> None:
    """Läuft in einem Hintergrundthread: `WORKFLOW.stream()` ist ein
    synchroner Generator und darf den Event-Loop des WS-Handlers nicht
    blockieren. Jeder Chunk wird threadsicher in die Queue des Event-Loops
    gereicht (Standard-Bridging-Pattern sync-Generator -> asyncio)."""
    try:
        for chunk in WORKFLOW.stream(graph_input, config=config, stream_mode="updates"):
            asyncio.run_coroutine_threadsafe(queue.put(("chunk", chunk)), loop)
    except Exception as exc:  # noqa: BLE001 - jeder Fehler muss den Client erreichen
        logger.exception("Fehler im Graph-Stream (session_id=%s)", config.get("configurable", {}).get("thread_id"))
        asyncio.run_coroutine_threadsafe(queue.put(("error", str(exc))), loop)
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(("done", None)), loop)


async def _stream_once(
    websocket: WebSocket,
    graph_input: Any,
    config: dict[str, Any],
    session_id: str,
) -> dict[str, Any] | None | Literal["cancelled"]:
    """Streamt einen einzelnen `WORKFLOW.stream()`-Lauf über die WS-Verbindung.

    Gibt das `Interrupt.value`-Payload zurück, falls der Lauf auf einer
    Eskalation pausiert wurde, `"cancelled"` bei Client-Abbruch, sonst `None`
    (regulär beendet oder Fehler).
    """
    loop = asyncio.get_event_loop()
    queue: "asyncio.Queue[tuple[str, Any]]" = asyncio.Queue()
    thread = threading.Thread(target=_run_stream_in_thread, args=(graph_input, config, loop, queue), daemon=True)
    thread.start()

    escalation_payload: dict[str, Any] | None = None
    last_completed_count = 0
    try:
        last_completed_count = len((_snapshot_run_state(session_id).get("completed_parts") or []))
    except Exception:  # noqa: BLE001
        pass

    while True:
        if session_id in CANCELLED_SESSIONS:
            CANCELLED_SESSIONS.discard(session_id)
            await websocket.send_json({"type": "cancelled", "session_id": session_id})
            return "cancelled"

        try:
            kind, payload = await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue

        if kind == "chunk":
            for node_name, node_value in payload.items():
                if node_name == "__interrupt__":
                    interrupt_obj = node_value[0] if node_value else None
                    escalation_payload = getattr(interrupt_obj, "value", interrupt_obj)
                    await websocket.send_json({"type": "escalation", "escalation": escalation_payload})
                    # Konzeptfoto früh persistieren, damit es nach Chat-Wechsel wieder da ist
                    try:
                        snap = WORKFLOW.get_state(config).values or {}
                        _persist_session_transcript(session_id, snap, status="human_approval_required")
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                await websocket.send_json({"type": "node_update", "node": node_name, "state": node_value})
                # Fertige Teile sofort in die Conversation kopieren (überlebt Standby/Cancel)
                if isinstance(node_value, dict) and "completed_parts" in node_value:
                    n = len(node_value.get("completed_parts") or [])
                    if n > last_completed_count:
                        last_completed_count = n
                        try:
                            snap = WORKFLOW.get_state(config).values or {}
                            _persist_artifacts_only(session_id, snap)
                        except Exception:  # noqa: BLE001
                            pass
        elif kind == "error":
            await websocket.send_json({"type": "error", "error": payload})
            return None
        elif kind == "done":
            return escalation_payload


def _snapshot_run_state(session_id: str) -> dict[str, Any]:
    """Liest den letzten Graph-State (Checkpoint oder leeres Dict)."""
    config = {"configurable": {"thread_id": session_id}}
    try:
        snap = WORKFLOW.get_state(config)
        return dict(snap.values or {}) if snap else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Checkpoint-Snapshot fehlgeschlagen (%s): %s", session_id, exc)
        return {}


def _secure_completed_progress(values: dict[str, Any]) -> dict[str, Any]:
    """Sichert ein bereits erfolgreiches aktuelles Teil in completed_parts.

    Wenn Validator SUCCESS war, der Supervisor den Part aber noch nicht
    abgeschlossen hat (Cancel/Disconnect dazwischen), würde das Teil sonst
    verloren gehen bzw. beim Resume doppelt verarbeitet.
    """
    snap = dict(values or {})
    sandbox = snap.get("sandbox_result")
    if not isinstance(sandbox, dict) or sandbox.get("status") != "SUCCESS":
        return snap

    parts = (snap.get("requirements_contract") or {}).get("parts") or []
    idx = int(snap.get("current_part_index") or 0)
    completed = list(snap.get("completed_parts") or [])
    if idx != len(completed):
        # Bereits vorgerückt oder inkonsistent – nicht erneut anhängen
        return snap
    if idx >= len(parts) and not parts:
        return snap

    part_name = parts[idx].get("name", f"Teil {idx + 1}") if idx < len(parts) else f"Teil {idx + 1}"
    completed.append(
        {
            "name": part_name,
            "part_contract": parts[idx] if idx < len(parts) else {},
            "stock_and_tool_context": snap.get("stock_and_tool_context"),
            "generated_code": snap.get("generated_code"),
            "sandbox_result": sandbox,
            "manufacturing_plan": snap.get("manufacturing_plan"),
        }
    )
    snap["completed_parts"] = completed
    snap["current_part_index"] = idx + 1
    snap["stock_and_tool_context"] = None
    snap["generated_code"] = None
    snap["sandbox_result"] = None
    snap["manufacturing_plan"] = None
    snap["manufacturing_assessed"] = False
    snap["manufacturing_feasibility"] = None
    snap["error_history"] = []
    snap["iteration_count"] = 0
    return snap


def _persist_artifacts_only(session_id: str, values: dict[str, Any]) -> None:
    """Kopiert STEP/STL/Konzept in die Conversation und Inventar-DB, ohne Chat-Nachricht."""
    conversation_id = _resolve_conversation_id(session_id, values)
    if not conversation_id or not values:
        return
    try:
        from app.db.postgres import SessionLocal
        from app.services import conversation_store

        db = SessionLocal()
        try:
            conversation_store.persist_session_artifacts(
                db, uuid.UUID(conversation_id), session_id, values
            )
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Artefakt-Zwischenpersistenz fehlgeschlagen: %s", exc)

    try:
        from app.services.cad_model_inventory import auto_persist_cad_models_inventory

        auto_persist_cad_models_inventory(
            session_id=session_id,
            conversation_id=conversation_id,
            values=values,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("3D→Inventar-Zwischenpersistenz fehlgeschlagen: %s", exc)


def _hydrate_for_resume(values: dict[str, Any], *, session_id: str) -> dict[str, Any]:
    """Baut einen Graph-Startzustand aus einem Pause-Snapshot (ohne Konzept-Reset)."""
    secured = _secure_completed_progress(values)
    hydrated = {k: v for k, v in secured.items() if k not in ("messages",)}
    hydrated["session_id"] = session_id
    hydrated["user_prompt"] = values.get("user_prompt") or "Fortsetzen des pausierten Workflows"
    # completed_parts und Konzept bleiben unverändert
    hydrated["completed_parts"] = list(secured.get("completed_parts") or [])
    hydrated["current_part_index"] = int(secured.get("current_part_index") or 0)

    has_contract = bool(hydrated.get("requirements_contract"))
    concept_approved = bool(hydrated.get("concept_approved"))
    vv_needs = bool(hydrated.get("vv_needs_alignment"))

    if vv_needs and not has_contract:
        hydrated["human_approval_required"] = True
        hydrated["escalation_reason"] = "requirements_approval"
    elif has_contract and not concept_approved:
        hydrated["human_approval_required"] = True
        hydrated["escalation_reason"] = "concept_approval"
    else:
        hydrated["human_approval_required"] = False
        if concept_approved or not vv_needs:
            if hydrated.get("escalation_reason") in ("concept_approval", "requirements_approval"):
                hydrated["escalation_reason"] = None

    # Fehlgeschlagene Sandbox → sauberer Retry, completed_parts bleiben
    sandbox = hydrated.get("sandbox_result")
    if isinstance(sandbox, dict) and sandbox.get("status") != "SUCCESS":
        hydrated["sandbox_result"] = None
        hydrated["generated_code"] = None

    hydrated["refinement_request"] = None
    hydrated["messages"] = [
        {"role": "user", "content": hydrated["user_prompt"]},
        {
            "role": "assistant",
            "content": (
                "[System] Workflow fortgesetzt – vorhandenes Konzept und "
                f"{len(hydrated.get('completed_parts') or [])} fertige Teil(e) bleiben erhalten."
            ),
        },
    ]
    return hydrated


def _remember_paused_run(
    session_id: str,
    values: dict[str, Any] | None = None,
    *,
    notify_chat: bool = True,
) -> dict[str, Any]:
    """Sichert Snapshot in Memory + Disk und persistiert 3D-Artefakte."""
    snap = _secure_completed_progress(values if values is not None else _snapshot_run_state(session_id))
    conversation_id = _resolve_conversation_id(session_id, snap)
    PAUSED_RUNS[session_id] = {
        "values": snap,
        "conversation_id": conversation_id,
    }
    try:
        paused_run_store.save_paused_run(session_id, snap, conversation_id=conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Disk-Pause-Snapshot fehlgeschlagen: %s", exc)

    _persist_artifacts_only(session_id, snap)

    if notify_chat and conversation_id and snap:
        try:
            from app.db.postgres import SessionLocal
            from app.services import conversation_store

            db = SessionLocal()
            try:
                title = (snap.get("requirements_contract") or {}).get("project_title") or "Workflow"
                n_done = len(snap.get("completed_parts") or [])
                conversation_store.add_message(
                    db,
                    uuid.UUID(conversation_id),
                    "assistant",
                    (
                        f"Workflow pausiert („{title}“)"
                        + (f" – {n_done} Teil(e) gesichert" if n_done else "")
                        + ". Mit „Fortsetzen“ lückenlos weitermachen."
                    ),
                    cad_session_id=session_id,
                    meta={
                        "status": "paused",
                        "resumable": True,
                        "concept_approved": bool(snap.get("concept_approved")),
                        "has_concept": bool(snap.get("requirements_contract")),
                        "current_part_index": snap.get("current_part_index", 0),
                        "completed_parts_count": n_done,
                    },
                )
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pause-Nachricht konnte nicht gespeichert werden: %s", exc)
    return snap


def _load_paused_bundle(session_id: str, conversation_id: str | None = None) -> dict[str, Any] | None:
    """Memory → Disk → Checkpoint, optional Conversation-Index."""
    paused = PAUSED_RUNS.get(session_id)
    if paused and (paused.get("values") or {}):
        return paused

    disk = paused_run_store.load_paused_run(session_id)
    if disk and (disk.get("values") or {}):
        PAUSED_RUNS[session_id] = {
            "values": disk.get("values") or {},
            "conversation_id": disk.get("conversation_id") or conversation_id,
        }
        return PAUSED_RUNS[session_id]

    if conversation_id:
        disk_conv = paused_run_store.load_paused_run_for_conversation(conversation_id)
        if disk_conv and (disk_conv.get("values") or {}):
            sid = disk_conv.get("session_id") or session_id
            bundle = {
                "values": disk_conv.get("values") or {},
                "conversation_id": conversation_id,
            }
            PAUSED_RUNS[sid] = bundle
            PAUSED_RUNS[session_id] = bundle
            return bundle

    snap = _snapshot_run_state(session_id)
    if snap:
        return {"values": snap, "conversation_id": conversation_id or _resolve_conversation_id(session_id, snap)}
    return None


@router.post("/cancel/{session_id}")
async def cancel_cad_session(session_id: str) -> dict[str, Any]:
    """Pausiert eine laufende CAD-Session (Snapshot + 3D-Artefakte bleiben erhalten)."""
    PENDING_SESSIONS.pop(session_id, None)
    # Snapshot VOR dem Cancel-Flag sichern (Stream kann parallel noch schreiben)
    snap = _remember_paused_run(session_id)
    CANCELLED_SESSIONS.add(session_id)
    values = snap or {}
    logger.info(
        "CAD-Session pausiert (session_id=%s, completed=%s)",
        session_id,
        len(values.get("completed_parts") or []),
    )
    return {
        "session_id": session_id,
        "status": "paused",
        "resumable": bool(values.get("requirements_contract") or values.get("user_prompt")),
        "concept_approved": bool(values.get("concept_approved")),
        "has_concept": bool(values.get("requirements_contract")),
        "completed_parts_count": len(values.get("completed_parts") or []),
    }


@router.post("/resume-run", response_model=CadResumeRunResponse)
async def resume_cad_run(request: CadResumeRunRequest) -> CadResumeRunResponse:
    """Setzt einen abgebrochenen/pausierten Workflow lückenlos fort (kein neues Konzept)."""
    session_id = request.session_id
    conversation_id = request.conversation_id

    bundle = _load_paused_bundle(session_id, conversation_id)
    values: dict[str, Any] = dict((bundle or {}).get("values") or {})
    conversation_id = conversation_id or (bundle or {}).get("conversation_id")

    if not values and not conversation_id:
        raise HTTPException(
            status_code=404,
            detail=f"Keine pausierte Session '{session_id}' zum Fortsetzen gefunden.",
        )

    if not values.get("user_prompt") and not values.get("requirements_contract"):
        raise HTTPException(
            status_code=409,
            detail="Pause-Snapshot enthält keinen fortsetzbaren Fortschritt.",
        )

    # Frischer Graph-Thread, aber gleiche asset-/Konzept-Session-ID im State
    run_id = str(uuid.uuid4())
    hydrated = _hydrate_for_resume(values, session_id=session_id)
    if conversation_id:
        hydrated["conversation_id"] = conversation_id
    # completed_parts bleiben im Hydrate; Artefakte der alten Session bleiben in der Conversation
    PENDING_SESSIONS[run_id] = hydrated
    CANCELLED_SESSIONS.discard(session_id)
    CANCELLED_SESSIONS.discard(run_id)

    if conversation_id:
        _bind_session_conversation(run_id, conversation_id)
        _bind_session_conversation(session_id, conversation_id)
        try:
            from app.db.postgres import SessionLocal
            from app.services import conversation_store

            db = SessionLocal()
            try:
                n_done = len(hydrated.get("completed_parts") or [])
                conversation_store.add_message(
                    db,
                    uuid.UUID(conversation_id),
                    "assistant",
                    (
                        "Workflow wird fortgesetzt"
                        + (f" ({n_done} fertige Teil(e) bleiben erhalten)" if n_done else "")
                        + "."
                    ),
                    cad_session_id=session_id,
                    meta={
                        "status": "resuming",
                        "run_id": run_id,
                        "resumed_from": session_id,
                        "completed_parts_count": n_done,
                    },
                )
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Resume-Nachricht konnte nicht gespeichert werden: %s", exc)

    PAUSED_RUNS.pop(session_id, None)
    try:
        paused_run_store.delete_paused_run(session_id)
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "CAD-Session fortgesetzt (from=%s, run_id=%s, completed=%s)",
        session_id,
        run_id,
        len(hydrated.get("completed_parts") or []),
    )
    contract = hydrated.get("requirements_contract") or {}
    return CadResumeRunResponse(
        session_id=run_id,
        resumed_from=session_id,
        status="pending",
        concept_approved=bool(hydrated.get("concept_approved")),
        has_concept=bool(hydrated.get("requirements_contract")),
        completed_parts=hydrated.get("completed_parts") or None,
        current_part_index=int(hydrated.get("current_part_index") or 0),
        total_parts=len(contract.get("parts") or []),
        concept_image_url=hydrated.get("concept_image_url"),
        requirements_contract=contract or None,
    )


class CadElaborateConceptRequest(BaseModel):
    conversation_id: str = Field(..., description="Chat-Unterhaltung mit dem Konzept-Artefakt")
    artifact_id: str = Field(..., description="concept_image-Artefakt, das ausgearbeitet werden soll")


class CadElaborateConceptResponse(BaseModel):
    session_id: str
    status: str = "pending"
    project_title: str | None = None
    resumed_from: str | None = None


@router.post("/elaborate-concept", response_model=CadElaborateConceptResponse)
async def elaborate_concept(request: CadElaborateConceptRequest) -> CadElaborateConceptResponse:
    """Startet die Ausarbeitung ab einem gespeicherten Konzept-Foto im Chat.

    Nutzt den im Artefakt gespeicherten Contract-Snapshot – bestehende Chat-
    Einträge bleiben unangetastet.
    """
    from app.db.postgres import SessionLocal
    from app.models.conversation import ConversationArtifact
    from app.services import conversation_store

    db = SessionLocal()
    try:
        art = db.get(ConversationArtifact, uuid.UUID(request.artifact_id))
        if not art or art.kind != "concept_image":
            raise HTTPException(status_code=404, detail="Konzept-Artefakt nicht gefunden.")
        if str(art.conversation_id) != request.conversation_id:
            raise HTTPException(status_code=400, detail="Artefakt gehört nicht zu dieser Unterhaltung.")

        meta = art.meta if isinstance(art.meta, dict) else {}
        contract = meta.get("requirements_contract")
        source_session = meta.get("session_id") or art.cad_session_id

        if (not isinstance(contract, dict) or not (contract.get("parts") or [])) and source_session:
            bundle = _load_paused_bundle(str(source_session), request.conversation_id)
            snap = (bundle or {}).get("values") or {}
            contract = snap.get("requirements_contract") or contract
            if not meta.get("user_prompt") and snap.get("user_prompt"):
                meta = {**meta, "user_prompt": snap.get("user_prompt")}
            if not meta.get("vv_requirements") and snap.get("vv_requirements"):
                meta = {**meta, "vv_requirements": snap.get("vv_requirements")}

        if not isinstance(contract, dict) or not (contract.get("parts") or []):
            raise HTTPException(
                status_code=409,
                detail="Für dieses Konzept fehlt der gespeicherte Requirements-Contract.",
            )

        user_prompt = (
            meta.get("user_prompt")
            or contract.get("raw_prompt")
            or f"Ausarbeitung von Konzept „{contract.get('project_title') or art.label}“"
        )

        run_id = str(uuid.uuid4())
        hydrated: dict[str, Any] = {
            "user_prompt": user_prompt,
            "session_id": source_session or run_id,
            "conversation_id": request.conversation_id,
            "requirements_contract": contract,
            "concept_sketch_svg": meta.get("concept_sketch_svg"),
            "concept_image_url": meta.get("concept_image_url")
            or f"/api/v1/conversations/artifacts/{art.id}/file",
            "concept_approved": True,
            "vv_requirements": meta.get("vv_requirements"),
            "vv_approved": True,
            "vv_needs_alignment": False,
            "vv_consulted_phases": ["concept"],
            "flexible_consulted": True,
            "current_part_index": 0,
            "completed_parts": [],
            "iteration_count": 0,
            "human_approval_required": False,
            "escalation_reason": None,
            "refinement_request": None,
            "messages": [
                {"role": "user", "content": user_prompt},
                {
                    "role": "assistant",
                    "content": (
                        f"[System] Ausarbeitung gestartet für Konzept „{contract.get('project_title') or art.label}“ "
                        f"(Artefakt {art.id})."
                    ),
                },
            ],
        }

        # Falls für die Quell-Session ein Pause-Snapshot existiert: fertige Teile übernehmen
        if source_session:
            bundle = _load_paused_bundle(source_session, request.conversation_id)
            snap = (bundle or {}).get("values") or {}
            if snap.get("completed_parts"):
                hydrated["completed_parts"] = list(snap["completed_parts"])
                hydrated["current_part_index"] = int(
                    snap.get("current_part_index") or len(hydrated["completed_parts"])
                )
            for key in (
                "flexible_specialist_profile",
                "advisory_notes",
                "vv_phase",
                "manufacturing_plan",
                "montage_result",
                "assembly_plan",
                "assembly_manual",
                "montage_assessed",
            ):
                if snap.get(key) is not None:
                    hydrated[key] = snap[key]
            consulted = list(snap.get("vv_consulted_phases") or ["concept"])
            if "concept" not in consulted:
                consulted.append("concept")
            hydrated["vv_consulted_phases"] = consulted

        PENDING_SESSIONS[run_id] = hydrated
        _bind_session_conversation(run_id, request.conversation_id)
        CANCELLED_SESSIONS.discard(run_id)

        title = contract.get("project_title") or art.label
        conversation_store.add_message(
            db,
            uuid.UUID(request.conversation_id),
            "assistant",
            f"Ausarbeitung gestartet für Konzept „{title}“.",
            cad_session_id=run_id,
            meta={
                "status": "elaborating",
                "artifact_id": str(art.id),
                "resumed_from": source_session,
                "ausarbeiten": True,
            },
        )
    finally:
        db.close()

    logger.info(
        "Konzept-Ausarbeitung gestartet (artifact=%s, run_id=%s, conversation=%s)",
        request.artifact_id,
        run_id,
        request.conversation_id,
    )
    return CadElaborateConceptResponse(
        session_id=run_id,
        status="pending",
        project_title=(contract.get("project_title") if isinstance(contract, dict) else None),
        resumed_from=source_session if isinstance(source_session, str) else None,
    )


@router.get("/concept-image/{session_id}")
async def get_concept_image(session_id: str) -> FileResponse:
    """Liefert das generierte Konzept-Foto einer Session (OpenAI Images)."""
    from app.services.concept_image import concept_image_path

    path = concept_image_path(session_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Kein Konzept-Foto für diese Session vorhanden.")
    return FileResponse(path=path, media_type="image/png", filename=path.name)


@router.websocket("/stream/{session_id}")
async def stream_cad_workflow(websocket: WebSocket, session_id: str) -> None:
    """Führt eine registrierte CAD-Session aus und streamt jeden Agenten-
    Node-Übergang live an den Client. Pausiert der Graph auf einer
    `interrupt()`-Eskalation, wird ein `escalation`-Event gesendet und auf
    eine `{"decision": ...}`-Nachricht des Clients gewartet, bevor der Graph
    fortgesetzt wird (SPEC Kap. 3.5, 5.2 Human-in-the-Loop-Dialog)."""
    await websocket.accept()
    config = {"configurable": {"thread_id": session_id}}

    graph_input = PENDING_SESSIONS.pop(session_id, None)
    if graph_input is None:
        # Keine frische Session registriert -> ggf. Reconnect auf eine bereits
        # pausierte (interrupted) Session; sonst existiert die Session nicht.
        snapshot = WORKFLOW.get_state(config)
        if not snapshot or not snapshot.values:
            await websocket.send_json(
                {"type": "error", "error": f"Session '{session_id}' ist nicht registriert und nicht pausiert."}
            )
            await websocket.close()
            return
        if not snapshot.next:
            await websocket.send_json({"type": "final", "result": _build_response(session_id, snapshot.values).model_dump()})
            await websocket.close()
            return
        graph_input = None  # Graph ist bereits pausiert; erster Schritt ist ein resume, kein neuer Input.

    try:
        with keep_system_awake(f"cad-stream:{session_id}"):
            while True:
                if session_id in CANCELLED_SESSIONS:
                    CANCELLED_SESSIONS.discard(session_id)
                    try:
                        snap = WORKFLOW.get_state(config).values or {}
                        if session_id not in PAUSED_RUNS:
                            _remember_paused_run(session_id, snap)
                        else:
                            _remember_paused_run(session_id, snap, notify_chat=False)
                        _persist_session_transcript(session_id, snap, status="cancelled")
                    except Exception:  # noqa: BLE001
                        pass
                    await websocket.send_json({"type": "cancelled", "session_id": session_id})
                    return

                escalation_payload = await _stream_once(websocket, graph_input, config, session_id)
                if escalation_payload == "cancelled":
                    try:
                        snap = WORKFLOW.get_state(config).values or {}
                        if session_id not in PAUSED_RUNS:
                            _remember_paused_run(session_id, snap)
                        else:
                            # Cancel-API hat schon Snapshot – Artefakte/Transcript nachziehen
                            secured = _secure_completed_progress(snap)
                            PAUSED_RUNS[session_id]["values"] = secured
                            _persist_artifacts_only(session_id, secured)
                            try:
                                paused_run_store.save_paused_run(
                                    session_id,
                                    secured,
                                    conversation_id=_resolve_conversation_id(session_id, secured),
                                )
                            except Exception:  # noqa: BLE001
                                pass
                        _persist_session_transcript(session_id, snap, status="cancelled")
                    except Exception:  # noqa: BLE001
                        pass
                    return
                if escalation_payload is None:
                    break  # regulär beendet oder Fehler

                # Eskalation: auf Nutzerentscheidung über dieselbe Verbindung warten.
                # Parallel Cancel prüfen (Client kann während Freigabe abbrechen).
                while True:
                    if session_id in CANCELLED_SESSIONS:
                        CANCELLED_SESSIONS.discard(session_id)
                        try:
                            snap = WORKFLOW.get_state(config).values or {}
                            if session_id not in PAUSED_RUNS:
                                _remember_paused_run(session_id, snap)
                            _persist_session_transcript(session_id, snap, status="cancelled")
                        except Exception:  # noqa: BLE001
                            pass
                        await websocket.send_json({"type": "cancelled", "session_id": session_id})
                        return
                    try:
                        message = await asyncio.wait_for(websocket.receive_json(), timeout=0.5)
                        break
                    except asyncio.TimeoutError:
                        continue
                    except WebSocketDisconnect:
                        logger.info("Client während Eskalation getrennt (session_id=%s)", session_id)
                        try:
                            snap = WORKFLOW.get_state(config).values or {}
                            _remember_paused_run(session_id, snap)
                            _persist_session_transcript(session_id, snap, status="cancelled")
                        except Exception:  # noqa: BLE001
                            pass
                        return
                decision = message.get("decision")
                graph_input = Command(resume=decision)

            final_state = WORKFLOW.get_state(config).values
            response = _build_response(session_id, final_state)
            _persist_session_transcript(session_id, final_state or {}, status=response.status)
            await websocket.send_json({"type": "final", "result": response.model_dump()})
    except WebSocketDisconnect:
        logger.info("Client getrennt – Session pausiert (session_id=%s)", session_id)
        try:
            snap = WORKFLOW.get_state(config).values or {}
            _remember_paused_run(session_id, snap)
            _persist_session_transcript(session_id, snap, status="cancelled")
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        CANCELLED_SESSIONS.discard(session_id)
        try:
            await websocket.close()
        except RuntimeError:
            pass  # Verbindung ggf. schon geschlossen


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/cad/download/{session_id}/{kind} – STEP/STL-Datei-Download
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/download/{session_id}/{kind}")
async def download_cad_export(session_id: str, kind: str, part_index: int | None = None) -> FileResponse:
    """Liefert die von der Sandbox exportierte STEP- oder STL-Datei einer
    Session aus. Der Pfad wird ausschließlich aus dem LangGraph-Checkpoint
    aufgelöst, niemals aus Client-Eingaben – das verhindert Path-Traversal
    (SPEC Kap. 4.1, 5.2 Download Center).

    Da der Supervisor `sandbox_result` nach jedem erfolgreich abgeschlossenen
    Teil zurücksetzt (Mehrteil-Ausarbeitung, Nutzer-Feedback), wird primär
    `completed_parts` durchsucht; `part_index` wählt ein bestimmtes Teil aus
    (Default: das zuletzt abgeschlossene). Der noch nicht abgeschlossene
    `sandbox_result` im Top-Level-State bleibt als Fallback erhalten (z. B.
    für synchrone `blocking=True`-Aufrufe ohne Mehrteil-Fortschritt)."""
    if kind not in ("step", "stl"):
        raise HTTPException(status_code=400, detail="kind muss 'step' oder 'stl' sein.")

    config = {"configurable": {"thread_id": session_id}}
    snapshot = WORKFLOW.get_state(config)
    values = dict(snapshot.values or {}) if snapshot else {}

    if not values:
        paused = PAUSED_RUNS.get(session_id) or paused_run_store.load_paused_run(session_id) or {}
        values = dict(paused.get("values") or {})

    completed_parts: list[dict[str, Any]] = values.get("completed_parts") or []
    export_paths: list[str] = []
    index = part_index
    if completed_parts:
        index = part_index if part_index is not None else len(completed_parts) - 1
        if 0 <= index < len(completed_parts):
            export_paths = (completed_parts[index].get("sandbox_result") or {}).get("export_paths") or []
    if not export_paths:
        sandbox_result = values.get("sandbox_result") or {}
        export_paths = sandbox_result.get("export_paths") or []

    match = next((p for p in export_paths if p.endswith(f".{kind}")), None)
    file_path = Path(match) if match else None

    # Fallback: Conversation-Artefakte (überleben Standby / Export-Cleanup)
    if file_path is None or not file_path.is_file():
        conversation_id = _resolve_conversation_id(session_id) or (
            (PAUSED_RUNS.get(session_id) or {}).get("conversation_id")
        )
        if not conversation_id:
            disk = paused_run_store.load_paused_run(session_id)
            conversation_id = (disk or {}).get("conversation_id")
        if conversation_id:
            try:
                from app.db.postgres import SessionLocal
                from app.models.conversation import ConversationArtifact

                db = SessionLocal()
                try:
                    q = db.query(ConversationArtifact).filter(
                        ConversationArtifact.conversation_id == uuid.UUID(conversation_id),
                        ConversationArtifact.kind == kind,
                    )
                    if index is not None:
                        q = q.filter(ConversationArtifact.part_index == index)
                    elif part_index is not None:
                        q = q.filter(ConversationArtifact.part_index == part_index)
                    art = q.order_by(ConversationArtifact.created_at.desc()).first()
                    if art and Path(art.file_path).is_file():
                        file_path = Path(art.file_path)
                finally:
                    db.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Download-Fallback über Conversation fehlgeschlagen: %s", exc)

    if file_path is None or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Keine .{kind}-Datei für diese Session vorhanden.")

    media_type = "model/step" if kind == "step" else "model/stl"
    return FileResponse(path=file_path, media_type=media_type, filename=file_path.name)
