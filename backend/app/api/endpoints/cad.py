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
# Mapping CAD-Session → Conversation für Artefakt-Persistenz
SESSION_CONVERSATIONS: dict[str, str] = {}


class CadGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=3, description="Freitext-Beschreibung des gewünschten Werkstücks")
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


class CadPendingResponse(BaseModel):
    session_id: str
    status: str = "pending"


class CadResumeRequest(BaseModel):
    session_id: str = Field(..., description="Session-ID eines pausierten Workflows (aus /generate)")
    decision: Any = Field(..., description="User-Entscheidung zur Fortsetzung nach einer Eskalation")


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


def _assistant_summary(values: dict[str, Any], status: str) -> str:
    """Kurze Nutzer-sichtbare Zusammenfassung (kein Sub-Agenten-Log)."""
    contract = values.get("requirements_contract") or {}
    title = contract.get("project_title") or "Auftrag"
    parts = contract.get("parts") or []
    completed = values.get("completed_parts") or []
    if status == "human_approval_required":
        return f"Konzept „{title}“ liegt zur Freigabe bereit ({len(parts)} Teil(e))."
    if status == "completed":
        names = ", ".join((p.get("name") or f"Teil {i+1}") for i, p in enumerate(completed)) or "keine Teile"
        return f"Ausarbeitung abgeschlossen: {title} · {len(completed)} Teil(e): {names}."
    if status == "cancelled":
        return "Vorgang abgebrochen."
    if status == "failed":
        return f"Ausarbeitung fehlgeschlagen ({title})."
    return f"Status: {status} ({title})."


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

    conversation_id = SESSION_CONVERSATIONS.get(session_id)
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
                conversation_store.add_message(
                    db,
                    conv_uuid,
                    "assistant",
                    _assistant_summary(values, status),
                    cad_session_id=session_id,
                    meta={"status": status},
                )
                # Transcript immer an den Chat hängen (auch bei Konzept-Freigabe /
                # Zwischenständen), damit der Logging-Reiter sie anzeigt.
                if txt_path and txt_path.is_file():
                    from app.models.conversation import ConversationArtifact
                    import shutil
                    from pathlib import Path

                    dest_dir = Path(settings.conversations_dir) / conversation_id
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / txt_path.name
                    shutil.copy2(txt_path, dest)
                    # Dedupliziere Transcript-Artefakt pro Session (neuestes .txt behalten)
                    existing_tx = (
                        db.query(ConversationArtifact)
                        .filter(
                            ConversationArtifact.conversation_id == conv_uuid,
                            ConversationArtifact.cad_session_id == session_id,
                            ConversationArtifact.kind == "transcript",
                        )
                        .first()
                    )
                    if not existing_tx:
                        db.add(
                            ConversationArtifact(
                                conversation_id=conv_uuid,
                                cad_session_id=session_id,
                                kind="transcript",
                                file_path=str(dest),
                                label=txt_path.name,
                            )
                        )
                        db.commit()
                    else:
                        # Alte Datei ggf. ersetzen, Label auf aktuelles Snapshot updaten
                        existing_tx.file_path = str(dest)
                        existing_tx.label = txt_path.name
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
    is_fully_done = total_parts > 0 and current_part_index >= total_parts

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
        SESSION_CONVERSATIONS[session_id] = request.conversation_id
        try:
            from app.db.postgres import SessionLocal
            from app.services import conversation_store

            db = SessionLocal()
            try:
                conv_uuid = uuid.UUID(request.conversation_id)
                conv = conversation_store.get_conversation(db, conv_uuid)
                if conv and conv.messages:
                    prior = [
                        f"{m.role.upper()}: {m.content}"
                        for m in conv.messages[-12:]
                        if m.content.strip()
                    ]
                    if prior:
                        prompt = (
                            "Bisheriger Unterhaltungskontext:\n"
                            + "\n".join(prior)
                            + "\n\nNeue Nutzeranweisung:\n"
                            + request.prompt
                        )
                # User-Nachricht dauerhaft in der Unterhaltung speichern
                conversation_store.add_message(
                    db,
                    conv_uuid,
                    "user",
                    request.prompt.strip(),
                    cad_session_id=session_id,
                )
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Konversation-Kontext konnte nicht geladen werden: %s", exc)

    initial_state = {
        "user_prompt": prompt,
        "session_id": session_id,
        "iteration_count": 0,
        "human_approval_required": False,
        "messages": [{"role": "user", "content": prompt}],
    }

    if request.blocking:
        config = {"configurable": {"thread_id": session_id}}
        logger.info("CAD-Workflow gestartet (blocking, session_id=%s)", session_id)
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
        elif kind == "error":
            await websocket.send_json({"type": "error", "error": payload})
            return None
        elif kind == "done":
            return escalation_payload


@router.post("/cancel/{session_id}")
async def cancel_cad_session(session_id: str) -> dict[str, str]:
    """Bricht eine laufende oder wartende CAD-Session ab (Nutzer-Feedback:
    jeder Prozess muss abbrechbar sein). Pending Sessions werden entfernt;
    laufende Streams prüfen `CANCELLED_SESSIONS` zwischen Chunks."""
    PENDING_SESSIONS.pop(session_id, None)
    CANCELLED_SESSIONS.add(session_id)
    logger.info("CAD-Session abgebrochen (session_id=%s)", session_id)
    return {"session_id": session_id, "status": "cancelled"}


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
        while True:
            if session_id in CANCELLED_SESSIONS:
                CANCELLED_SESSIONS.discard(session_id)
                try:
                    snap = WORKFLOW.get_state(config).values or {}
                    _persist_session_transcript(session_id, snap, status="cancelled")
                except Exception:  # noqa: BLE001
                    pass
                await websocket.send_json({"type": "cancelled", "session_id": session_id})
                return

            escalation_payload = await _stream_once(websocket, graph_input, config, session_id)
            if escalation_payload == "cancelled":
                try:
                    snap = WORKFLOW.get_state(config).values or {}
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
                    return
            decision = message.get("decision")
            graph_input = Command(resume=decision)

        final_state = WORKFLOW.get_state(config).values
        response = _build_response(session_id, final_state)
        _persist_session_transcript(session_id, final_state or {}, status=response.status)
        await websocket.send_json({"type": "final", "result": response.model_dump()})
    except WebSocketDisconnect:
        logger.info("Client getrennt (session_id=%s)", session_id)
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
    values = snapshot.values if snapshot else {}
    if not values:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' nicht gefunden.")

    completed_parts: list[dict[str, Any]] = values.get("completed_parts") or []
    export_paths: list[str] = []
    if completed_parts:
        index = part_index if part_index is not None else len(completed_parts) - 1
        if not (0 <= index < len(completed_parts)):
            raise HTTPException(status_code=404, detail=f"Kein Teil mit Index {index} vorhanden.")
        export_paths = (completed_parts[index].get("sandbox_result") or {}).get("export_paths") or []
    else:
        sandbox_result = values.get("sandbox_result") or {}
        export_paths = sandbox_result.get("export_paths") or []

    match = next((p for p in export_paths if p.endswith(f".{kind}")), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Keine .{kind}-Datei für diese Session vorhanden.")

    file_path = Path(match)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Export-Datei existiert nicht (mehr) auf dem Server.")

    media_type = "model/step" if kind == "step" else "model/stl"
    return FileResponse(path=file_path, media_type=media_type, filename=file_path.name)
