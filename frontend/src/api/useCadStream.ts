import { useCallback, useMemo, useRef, useState } from "react";

import { api, wsUrl } from "./client";
import type {
  AgentNodeName,
  AgentTranscriptEntry,
  CadStreamMessage,
  CadWorkflowResult,
  CompletedPart,
  ConceptDecision,
  EscalationPayload,
  RequirementsContract,
} from "./types";

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
  agentTranscript: AgentTranscriptEntry[];
  completedParts: CompletedPart[];
  currentPartIndex: number;
  totalParts: number;
  currentPartName: string | null;
  /** Startet einen Workflow-Turn; löscht nicht den Chat-Verlauf (nur Run-State). */
  start: (prompt: string, conversationId?: string | null) => Promise<string | null>;
  resolveEscalation: (decision: ConceptDecision | unknown) => void;
  cancel: () => Promise<void>;
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
  const [currentNode, setCurrentNode] = useState<AgentNodeName | null>(null);
  const [logs, setLogs] = useState<AgentLogEntry[]>([]);
  const [escalation, setEscalation] = useState<EscalationPayload | null>(null);
  const [result, setResult] = useState<CadWorkflowResult | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [latestState, setLatestState] = useState<Record<string, unknown>>({});

  const socketRef = useRef<WebSocket | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  const clearRun = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
    sessionIdRef.current = null;
    setStatus("idle");
    setSessionId(null);
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
          setLogs((prev) => [...prev, { node: message.node, state: message.state, timestamp: Date.now() }]);
          setLatestState((prev) => {
            const next: Record<string, unknown> = { ...prev, ...message.state };
            if (Array.isArray(message.state.agent_transcript)) {
              next.agent_transcript = [
                ...((prev.agent_transcript as unknown[]) ?? []),
                ...message.state.agent_transcript,
              ];
            }
            return next;
          });
          break;
        case "escalation":
          setStatus("escalation");
          setEscalation(message.escalation);
          break;
        case "final":
          setStatus(message.result.status === "completed" ? "completed" : "failed");
          setResult(message.result);
          setEscalation(null);
          break;
        case "cancelled":
          setStatus("cancelled");
          setEscalation(null);
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
      socketRef.current = null;
    };
  }, []);

  const start = useCallback(
    async (prompt: string, conversationId?: string | null) => {
      // Nur Run-State zurücksetzen – Chat bleibt in der Conversation-API.
      socketRef.current?.close();
      socketRef.current = null;
      setLogs([]);
      setEscalation(null);
      setResult(null);
      setErrorMessage(null);
      setLatestState({});
      setCurrentNode(null);
      setStatus("connecting");
      try {
        const pending = await api.post<{ session_id: string; status: string }>("/api/v1/cad/generate", {
          prompt,
          conversation_id: conversationId || undefined,
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
    setStatus("cancelled");
    setEscalation(null);
    socketRef.current?.close();
    socketRef.current = null;
    if (sid) {
      try {
        await api.post(`/api/v1/cad/cancel/${sid}`, {});
      } catch {
        // UI ist bereits cancelled
      }
    }
  }, [sessionId]);

  const effectiveState = (result as unknown as Record<string, unknown>) ?? latestState;

  const conceptSketchSvg = useMemo(
    () => (effectiveState.concept_sketch_svg as string | undefined) ?? escalation?.concept_sketch_svg ?? null,
    [effectiveState, escalation],
  );
  const conceptImageUrl = useMemo(
    () => (effectiveState.concept_image_url as string | undefined) ?? escalation?.concept_image_url ?? null,
    [effectiveState, escalation],
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
    agentTranscript,
    completedParts,
    currentPartIndex,
    totalParts,
    currentPartName,
    start,
    resolveEscalation,
    cancel,
    clearRun,
  };
}
