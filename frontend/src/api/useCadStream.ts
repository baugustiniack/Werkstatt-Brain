import { useCallback, useMemo, useRef, useState } from "react";

import { api, wsUrl } from "./client";
import type {
  AgentNodeName,
  AgentTranscriptEntry,
  CadStreamMessage,
  CadWorkflowResult,
  CompletedPart,
  ConceptDecision,
  ConceptRosterMode,
  EscalationPayload,
  RequirementsContract,
} from "./types";
import { useWorkflowWakeLock } from "../hooks/useWorkflowWakeLock";

export type CadRunStatus =
  | "idle"
  | "connecting"
  | "running"
  | "escalation"
  | "completed"
  | "failed"
  | "cancelled"
  | "error";

export interface AgentLogEntry {
  node: AgentNodeName;
  state: Record<string, unknown>;
  timestamp: number;
}

export interface UseCadStreamResult {
  status: CadRunStatus;
  sessionId: string | null;
  currentNode: AgentNodeName | null;
  logs: AgentLogEntry[];
  escalation: EscalationPayload | null;
  result: CadWorkflowResult | null;
  errorMessage: string | null;
  conceptSketchSvg: string | null;
  conceptImageUrl: string | null;
  conceptImageUrls: NonNullable<EscalationPayload["concept_image_urls"]>;
  conceptTitle: string | null;
  conceptPanelGrades: NonNullable<EscalationPayload["concept_panel_grades"]>;
  conceptPanelAverage: number | null;
  conceptPanelRound: number;
  conceptPanelQueue: string[];
  panelReviewerId: string | null;
  conceptRoster: string[];
  conceptPanelAwaitingRebuild: boolean;
  conceptPanelReverted: boolean;
  agentTranscript: AgentTranscriptEntry[];
  completedParts: CompletedPart[];
  currentPartIndex: number;
  totalParts: number;
  currentPartName: string | null;
  /** Startet einen Workflow-Turn; löscht nicht den Chat-Verlauf (nur Run-State). */
  start: (
    prompt: string,
    conversationId?: string | null,
    opts?: { persistUserMessage?: boolean },
  ) => Promise<string | null>;
  /** Setzt eine pausierte Session lückenlos fort (kein neues Konzept). */
  resume: (sessionId: string, conversationId?: string | null) => Promise<string | null>;
  /** Startet Ausarbeitung ab einem gespeicherten Konzept-Foto im Chat. */
  elaborateConcept: (conversationId: string, artifactId: string) => Promise<string | null>;
  resolveEscalation: (decision: ConceptDecision | unknown) => void;
  cancel: () => Promise<void>;
  /** Session-ID der zuletzt pausierten Session (für Fortsetzen). */
  pausedSessionId: string | null;
  /** Setzt nur den laufenden CAD-Run zurück (nicht die Unterhaltung). */
  clearRun: () => void;
}

/**
 * Verbindet sich mit dem WebSocket-Live-Stream des CAD-Agenten-Workflows.
 * Chat-Historie liegt in der Conversation-API – dieser Hook hält nur den aktiven Run.
 */
export function useCadStream(): UseCadStreamResult {
  const [status, setStatus] = useState<CadRunStatus>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [pausedSessionId, setPausedSessionId] = useState<string | null>(null);
  const [currentNode, setCurrentNode] = useState<AgentNodeName | null>(null);
  const [logs, setLogs] = useState<AgentLogEntry[]>([]);
  const [escalation, setEscalation] = useState<EscalationPayload | null>(null);
  const [result, setResult] = useState<CadWorkflowResult | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [latestState, setLatestState] = useState<Record<string, unknown>>({});

  const socketRef = useRef<WebSocket | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const pausedSessionIdRef = useRef<string | null>(null);

  useWorkflowWakeLock(status === "connecting" || status === "running" || status === "escalation");

  const clearRun = useCallback(() => {
    const prevSocket = socketRef.current;
    socketRef.current = null;
    prevSocket?.close();
    sessionIdRef.current = null;
    pausedSessionIdRef.current = null;
    setStatus("idle");
    setSessionId(null);
    setPausedSessionId(null);
    setCurrentNode(null);
    setLogs([]);
    setEscalation(null);
    setResult(null);
    setErrorMessage(null);
    setLatestState({});
  }, []);

  const openStream = useCallback((session_id: string) => {
    const socket = new WebSocket(wsUrl(`/api/v1/cad/stream/${session_id}`));
    socketRef.current = socket;

    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as CadStreamMessage;

      switch (message.type) {
        case "node_update":
          setCurrentNode(message.node);
          setLogs((prev) => {
            // Nur schlanke Logs – volle Agent-States puffen den Browser-RAM
            const slim: Record<string, unknown> = {};
            for (const key of ["error", "current_part_name", "iteration_count", "status"]) {
              if (key in message.state) slim[key] = message.state[key];
            }
            const next = [...prev, { node: message.node, state: slim, timestamp: Date.now() }];
            return next.length > 40 ? next.slice(-40) : next;
          });
          setLatestState((prev) => {
            const next: Record<string, unknown> = { ...prev, ...message.state };
            if (Array.isArray(message.state.agent_transcript)) {
              const merged = [
                ...((prev.agent_transcript as unknown[]) ?? []),
                ...message.state.agent_transcript,
              ];
              // Transcript begrenzen
              next.agent_transcript = merged.length > 80 ? merged.slice(-80) : merged;
            }
            // completed_parts nie durch leere Updates verlieren
            if (
              !Array.isArray(message.state.completed_parts) &&
              Array.isArray(prev.completed_parts) &&
              (prev.completed_parts as unknown[]).length > 0
            ) {
              next.completed_parts = prev.completed_parts;
            }
            if (
              Array.isArray(message.state.concept_image_urls) &&
              (message.state.concept_image_urls as unknown[]).length === 0 &&
              Array.isArray(prev.concept_image_urls) &&
              (prev.concept_image_urls as unknown[]).length > 0
            ) {
              next.concept_image_urls = prev.concept_image_urls;
            }
            if (!message.state.concept_image_url && prev.concept_image_url) {
              next.concept_image_url = prev.concept_image_url;
            }
            return next;
          });
          break;
        case "escalation": {
          setStatus("escalation");
          const raw = message.escalation as EscalationPayload | string | null | unknown[];
          let esc: EscalationPayload;
          if (typeof raw === "string") {
            esc = {
              reason: raw,
              question: raw.includes(" ") ? raw : "Bitte Entscheidung treffen.",
            };
          } else if (Array.isArray(raw) && raw[0] && typeof raw[0] === "object") {
            const first = raw[0] as EscalationPayload & { value?: EscalationPayload };
            const inner = first.value && typeof first.value === "object" ? first.value : first;
            esc = {
              ...inner,
              reason: String(inner.reason || "").trim() || "unknown",
            };
          } else if (raw && typeof raw === "object") {
            const obj = raw as EscalationPayload & { value?: EscalationPayload };
            const inner = obj.value && typeof obj.value === "object" ? obj.value : obj;
            esc = {
              ...inner,
              reason: String(inner.reason || "").trim() || "unknown",
            };
          } else {
            esc = { reason: "unknown", question: "Entscheidung nötig – Payload fehlte." };
          }
          if (
            (esc.reason === "requirements_question" || esc.reason === "concept_clarification") &&
            !(esc.question || "").trim()
          ) {
            const must = esc.coherence_critique?.must_ask_user?.[0];
            const oq = esc.vv_requirements?.open_questions?.[0];
            esc = {
              ...esc,
              question: String(must || oq || "Bitte die offene Frage beantworten."),
            };
          }
          setEscalation(esc);
          setLatestState((prev) => ({
            ...prev,
            ...(esc.concept_image_url ? { concept_image_url: esc.concept_image_url } : {}),
            ...(esc.concept_image_urls ? { concept_image_urls: esc.concept_image_urls } : {}),
            ...(esc.concept_panel_grades ? { concept_panel_grades: esc.concept_panel_grades } : {}),
            ...(esc.concept_panel_average != null ? { concept_panel_average: esc.concept_panel_average } : {}),
            ...(esc.concept_panel_round != null ? { concept_panel_round: esc.concept_panel_round } : {}),
            ...(esc.concept_roster ? { concept_roster: esc.concept_roster } : {}),
            ...(esc.concept_panel_reverted != null ? { concept_panel_reverted: esc.concept_panel_reverted } : {}),
            ...(esc.requirements_contract ? { requirements_contract: esc.requirements_contract } : {}),
          }));
          break;
        }
        case "final":
          setStatus(message.result.status === "completed" ? "completed" : "failed");
          setResult(message.result);
          setEscalation(null);
          break;
        case "cancelled":
          setStatus("cancelled");
          setEscalation(null);
          if (sessionIdRef.current) {
            setPausedSessionId(sessionIdRef.current);
            pausedSessionIdRef.current = sessionIdRef.current;
          }
          break;
        case "error":
          setStatus("error");
          setErrorMessage(message.error);
          break;
      }
    };

    socket.onerror = () => {
      setStatus((prev) => (prev === "cancelled" ? prev : "error"));
      setErrorMessage((prev) => prev ?? "WebSocket-Verbindung fehlgeschlagen.");
    };

    socket.onclose = () => {
      // Nur unerwartete Trennung (Standby) → Pause; bewusstes close() nullt socketRef vorher
      if (socketRef.current !== socket) {
        return;
      }
      socketRef.current = null;
      setStatus((prev) => {
        if (prev === "running" || prev === "connecting" || prev === "escalation") {
          if (sessionIdRef.current) {
            setPausedSessionId(sessionIdRef.current);
            pausedSessionIdRef.current = sessionIdRef.current;
          }
          return "cancelled";
        }
        return prev;
      });
    };
  }, []);

  const start = useCallback(
    async (
      prompt: string,
      conversationId?: string | null,
      opts?: {
        persistUserMessage?: boolean;
        conceptRosterMode?: ConceptRosterMode;
        conceptRoster?: string[];
      },
    ) => {
      // Nur Run-State zurücksetzen – Chat bleibt in der Conversation-API.
      const prevSocket = socketRef.current;
      socketRef.current = null;
      prevSocket?.close();
      setLogs([]);
      setEscalation(null);
      setResult(null);
      setErrorMessage(null);
      setLatestState({});
      setCurrentNode(null);
      setPausedSessionId(null);
      pausedSessionIdRef.current = null;
      setStatus("connecting");
      try {
        const pending = await api.post<{ session_id: string; status: string }>("/api/v1/cad/generate", {
          prompt,
          conversation_id: conversationId || undefined,
          persist_user_message: opts?.persistUserMessage !== false,
          ...(opts?.conceptRosterMode ? { concept_roster_mode: opts.conceptRosterMode } : {}),
          ...(opts?.conceptRosterMode === "manual" && opts.conceptRoster?.length
            ? { concept_roster: opts.conceptRoster }
            : {}),
        });
        sessionIdRef.current = pending.session_id;
        setSessionId(pending.session_id);
        setStatus("running");
        openStream(pending.session_id);
        return pending.session_id;
      } catch (err) {
        setStatus("error");
        setErrorMessage(err instanceof Error ? err.message : "Unbekannter Fehler beim Start des Workflows.");
        return null;
      }
    },
    [openStream],
  );

  const resume = useCallback(
    async (pausedId: string, conversationId?: string | null) => {
      const prevSocket = socketRef.current;
      socketRef.current = null;
      prevSocket?.close();
      setLogs([]);
      setEscalation(null);
      setResult(null);
      setErrorMessage(null);
      setCurrentNode(null);
      setStatus("connecting");
      try {
        const pending = await api.post<{
          session_id: string;
          resumed_from: string;
          status: string;
          completed_parts?: CompletedPart[] | null;
          current_part_index?: number;
          concept_image_url?: string | null;
          concept_image_urls?: EscalationPayload["concept_image_urls"];
          concept_panel_grades?: EscalationPayload["concept_panel_grades"];
          concept_panel_average?: number | null;
          concept_panel_round?: number | null;
          concept_panel_queue?: string[] | null;
          panel_reviewer_id?: string | null;
          concept_roster?: string[] | null;
          concept_panel_awaiting_rebuild?: boolean | null;
          concept_panel_reverted?: boolean | null;
          agent_transcript?: AgentTranscriptEntry[] | null;
          requirements_contract?: RequirementsContract | null;
        }>("/api/v1/cad/resume-run", {
          session_id: pausedId,
          conversation_id: conversationId || undefined,
        });
        sessionIdRef.current = pending.session_id;
        setSessionId(pending.session_id);
        setPausedSessionId(null);
        pausedSessionIdRef.current = null;
        // Fertige Teile + Konzept-Stand sofort wieder in den Viewer laden
        setLatestState((prev) => ({
          ...prev,
          completed_parts: pending.completed_parts ?? prev.completed_parts ?? [],
          current_part_index: pending.current_part_index ?? prev.current_part_index ?? 0,
          concept_image_url: pending.concept_image_url ?? prev.concept_image_url,
          concept_image_urls: pending.concept_image_urls ?? prev.concept_image_urls,
          concept_panel_grades: pending.concept_panel_grades ?? prev.concept_panel_grades,
          concept_panel_average: pending.concept_panel_average ?? prev.concept_panel_average,
          concept_panel_round: pending.concept_panel_round ?? prev.concept_panel_round,
          concept_panel_queue: pending.concept_panel_queue ?? prev.concept_panel_queue,
          panel_reviewer_id: pending.panel_reviewer_id ?? prev.panel_reviewer_id,
          concept_roster: pending.concept_roster ?? prev.concept_roster,
          concept_panel_awaiting_rebuild:
            pending.concept_panel_awaiting_rebuild ?? prev.concept_panel_awaiting_rebuild,
          concept_panel_reverted: pending.concept_panel_reverted ?? prev.concept_panel_reverted,
          agent_transcript: pending.agent_transcript ?? prev.agent_transcript,
          requirements_contract: pending.requirements_contract ?? prev.requirements_contract,
        }));
        setStatus("running");
        openStream(pending.session_id);
        return pending.session_id;
      } catch (err) {
        setStatus("cancelled");
        setPausedSessionId(pausedId);
        pausedSessionIdRef.current = pausedId;
        setErrorMessage(err instanceof Error ? err.message : "Fortsetzen fehlgeschlagen.");
        return null;
      }
    },
    [openStream],
  );

  const elaborateConcept = useCallback(
    async (conversationId: string, artifactId: string) => {
      const prevSocket = socketRef.current;
      socketRef.current = null;
      prevSocket?.close();
      setLogs([]);
      setEscalation(null);
      setResult(null);
      setErrorMessage(null);
      setCurrentNode(null);
      setStatus("connecting");
      try {
        const pending = await api.post<{ session_id: string; status: string }>(
          "/api/v1/cad/elaborate-concept",
          {
            conversation_id: conversationId,
            artifact_id: artifactId,
          },
        );
        sessionIdRef.current = pending.session_id;
        setSessionId(pending.session_id);
        setPausedSessionId(null);
        pausedSessionIdRef.current = null;
        setStatus("running");
        openStream(pending.session_id);
        return pending.session_id;
      } catch (err) {
        setStatus("error");
        setErrorMessage(err instanceof Error ? err.message : "Ausarbeitung fehlgeschlagen.");
        return null;
      }
    },
    [openStream],
  );

  const resolveEscalation = useCallback((decision: ConceptDecision | unknown) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setErrorMessage("Keine aktive Verbindung, um die Eskalation zu beantworten.");
      return;
    }
    socket.send(JSON.stringify({ decision }));
    setStatus("running");
    setEscalation(null);
  }, []);

  const cancel = useCallback(async () => {
    const sid = sessionIdRef.current ?? sessionId;
    if (sid) {
      setPausedSessionId(sid);
      pausedSessionIdRef.current = sid;
      try {
        // Zuerst Backend-Snapshot, dann WS schließen (sonst geht der Pause-State verloren)
        await api.post(`/api/v1/cad/cancel/${sid}`, {});
      } catch {
        // UI ist bereits pausiert
      }
    }
    setStatus("cancelled");
    setEscalation(null);
    const prevSocket = socketRef.current;
    socketRef.current = null;
    prevSocket?.close();
  }, [sessionId]);

  const effectiveState = useMemo(
    () => ({ ...((result as Record<string, unknown> | null) ?? {}), ...latestState }),
    [latestState, result],
  );

  const conceptSketchSvg = useMemo(
    () => (effectiveState.concept_sketch_svg as string | undefined) ?? escalation?.concept_sketch_svg ?? null,
    [effectiveState, escalation],
  );
  const conceptImageUrl = useMemo(
    () => (effectiveState.concept_image_url as string | undefined) ?? escalation?.concept_image_url ?? null,
    [effectiveState, escalation],
  );
  const conceptImageUrls = useMemo(() => {
    const fromState = effectiveState.concept_image_urls as EscalationPayload["concept_image_urls"];
    const fromEsc = escalation?.concept_image_urls;
    return (fromState?.length ? fromState : fromEsc) ?? [];
  }, [effectiveState, escalation]);
  const conceptTitle = useMemo(() => {
    const contract = effectiveState.requirements_contract as RequirementsContract | undefined;
    return contract?.project_title || escalation?.requirements_contract?.project_title || null;
  }, [effectiveState, escalation]);
  const conceptPanelGrades = useMemo(() => {
    const fromState = effectiveState.concept_panel_grades as EscalationPayload["concept_panel_grades"];
    const fromEsc = escalation?.concept_panel_grades;
    return (fromState?.length ? fromState : fromEsc) ?? [];
  }, [effectiveState, escalation]);
  const conceptPanelAverage = useMemo(() => {
    const fromState = effectiveState.concept_panel_average;
    if (typeof fromState === "number") return fromState;
    return escalation?.concept_panel_average ?? null;
  }, [effectiveState, escalation]);
  const conceptPanelRound = useMemo(
    () => Number(effectiveState.concept_panel_round || escalation?.concept_panel_round || 0),
    [effectiveState, escalation],
  );
  const conceptPanelQueue = useMemo(() => {
    const q = effectiveState.concept_panel_queue;
    return Array.isArray(q) ? q.map(String) : [];
  }, [effectiveState]);
  const panelReviewerId = useMemo(() => {
    const id = effectiveState.panel_reviewer_id;
    return typeof id === "string" && id ? id : null;
  }, [effectiveState]);
  const conceptRoster = useMemo(() => {
    const fromState = effectiveState.concept_roster;
    const fromEsc = escalation?.concept_roster;
    const list = (Array.isArray(fromState) && fromState.length ? fromState : fromEsc) ?? [];
    return list.map(String);
  }, [effectiveState, escalation]);
  const conceptPanelAwaitingRebuild = Boolean(effectiveState.concept_panel_awaiting_rebuild);
  const conceptPanelReverted = Boolean(
    effectiveState.concept_panel_reverted ?? escalation?.concept_panel_reverted,
  );
  const agentTranscript = useMemo(() => {
    const fromResult = (result as CadWorkflowResult | null)?.agent_transcript;
    if (fromResult && fromResult.length > 0) return fromResult;
    return (effectiveState.agent_transcript as AgentTranscriptEntry[] | undefined) ?? [];
  }, [effectiveState, result]);
  const completedParts = useMemo(
    () => (effectiveState.completed_parts as CompletedPart[] | undefined) ?? [],
    [effectiveState],
  );
  const currentPartIndex = useMemo(() => (effectiveState.current_part_index as number | undefined) ?? 0, [effectiveState]);
  const contractParts = useMemo(
    () => (effectiveState.requirements_contract as RequirementsContract | undefined)?.parts ?? [],
    [effectiveState],
  );
  const totalParts = contractParts.length;
  const currentPartName = contractParts[currentPartIndex]?.name ?? null;

  return {
    status,
    sessionId,
    currentNode,
    logs,
    escalation,
    result,
    errorMessage,
    conceptSketchSvg,
    conceptImageUrl,
    conceptImageUrls,
    conceptTitle,
    conceptPanelGrades,
    conceptPanelAverage,
    conceptPanelRound,
    conceptPanelQueue,
    panelReviewerId,
    conceptRoster,
    conceptPanelAwaitingRebuild,
    conceptPanelReverted,
    agentTranscript,
    completedParts,
    currentPartIndex,
    totalParts,
    currentPartName,
    start,
    resume,
    elaborateConcept,
    resolveEscalation,
    cancel,
    pausedSessionId,
    clearRun,
  };
}
