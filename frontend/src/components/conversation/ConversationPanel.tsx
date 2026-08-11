import { useEffect, useMemo, useRef, useState, type FormEvent, type MouseEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { apiBaseUrl } from "../../api/client";
import type { ConceptDecision, EscalationPayload } from "../../api/types";
import type { CadRunStatus, UseCadStreamResult } from "../../api/useCadStream";
import {
  invalidateConversation,
  useConversation,
  useConversations,
  useCreateConversation,
  useDeleteConversation,
  type ConversationArtifact,
} from "../../hooks/useConversations";
import { StatusBadge } from "../layout/StatusBadge";
import { ReferenceUpload } from "../commandCenter/ReferenceUpload";

const ACTIVE_CONV_KEY = "werkstatt.activeConversationId";

function resolveMediaUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.startsWith("http") ? url : `${apiBaseUrl()}${url}`;
}

type ContractParts = NonNullable<EscalationPayload["requirements_contract"]>["parts"];

function ConceptImageCard({
  title,
  imageUrl,
  parts,
  interactive,
  onDecision,
  onElaborate,
  elaborating,
}: {
  title: string;
  imageUrl: string | null;
  parts?: ContractParts;
  interactive?: boolean;
  onDecision?: (decision: ConceptDecision) => void;
  /** Historisches Konzept: Ausarbeitung neu starten */
  onElaborate?: () => void;
  elaborating?: boolean;
}) {
  const [mode, setMode] = useState<"view" | "revise">("view");
  const [feedback, setFeedback] = useState("");
  const [partsOpen, setPartsOpen] = useState(false);
  const partList = parts ?? [];

  return (
    <div className="rounded-lg border border-workshop-accent/60 bg-workshop-bg/80 p-3">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="text-xs font-semibold text-workshop-accent">
          {interactive ? `Konzept zur Freigabe: ${title}` : `Konzept-Foto: ${title}`}
        </div>
        <span
          className="shrink-0 rounded border border-workshop-border px-1.5 py-0.5 text-[10px] text-workshop-muted"
          title="Automatisch in Inventar-DB gespeichert"
        >
          KI-Generiert
        </span>
      </div>

      {imageUrl ? (
        <a href={imageUrl} target="_blank" rel="noreferrer" className="block">
          <img
            src={imageUrl}
            alt={title}
            className="mb-3 max-h-72 w-full rounded-md border border-workshop-border object-cover"
          />
        </a>
      ) : (
        <div className="mb-3 flex max-h-48 min-h-32 items-center justify-center rounded-md border border-dashed border-workshop-border bg-black/20 p-4 text-center text-xs text-workshop-muted">
          Kein Raumfoto verfügbar. Cursor kann keine Bilder erzeugen – bitte einen{" "}
          <strong className="text-workshop-text">OpenAI-Key</strong> unter Einstellungen hinterlegen
          (Images API). Unten die technische Teileliste.
        </div>
      )}

      {partList.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setPartsOpen((v) => !v)}
            className="mb-2 text-xs font-semibold text-workshop-muted hover:text-workshop-text"
          >
            {partsOpen ? "▾" : "▸"} Technische Teileliste ({partList.length})
          </button>
          {partsOpen && (
            <div className="mb-3 max-h-40 overflow-y-auto rounded-md border border-workshop-border p-2 text-xs">
              {partList.map((part, idx) => (
                <div key={idx} className="border-b border-workshop-border/50 py-1 last:border-0">
                  <span className="font-semibold text-workshop-text">
                    {idx + 1}. {part.name}
                  </span>
                  <span className="text-workshop-muted">
                    {" "}
                    · {part.functional_geometry?.dimensions_mm?.x}×{part.functional_geometry?.dimensions_mm?.y}×
                    {part.functional_geometry?.dimensions_mm?.z}mm · {part.material_tool_constraints?.material_type}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {interactive && onDecision && (
        <>
          {mode === "revise" ? (
            <div className="flex flex-col gap-2">
              <textarea
                value={feedback}
                onChange={(event) => setFeedback(event.target.value)}
                placeholder="Was soll am Entwurf geändert werden?"
                rows={2}
                className="w-full resize-none rounded-md border border-workshop-border bg-workshop-panel p-2 text-xs"
              />
              <div className="flex justify-end gap-2">
                <button type="button" onClick={() => setMode("view")} className="text-xs text-workshop-muted hover:underline">
                  Zurück
                </button>
                <button
                  type="button"
                  disabled={!feedback.trim()}
                  onClick={() => onDecision({ decision: "revise", feedback: feedback.trim() })}
                  className="rounded-md bg-workshop-warning px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
                >
                  Änderungen anfordern
                </button>
              </div>
            </div>
          ) : (
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setMode("revise")}
                className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-panel"
              >
                Änderungen anfordern
              </button>
              <button
                type="button"
                onClick={() => onDecision({ decision: "approve" })}
                className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg"
              >
                Freigeben &amp; ausarbeiten
              </button>
            </div>
          )}
        </>
      )}

      {!interactive && onElaborate && (
        <div className="flex justify-end">
          <button
            type="button"
            disabled={elaborating}
            onClick={() => onElaborate()}
            className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
            title="3D-Ausarbeitung für dieses Konzept starten"
          >
            {elaborating ? "Startet…" : "Ausarbeiten"}
          </button>
        </div>
      )}
    </div>
  );
}

export interface ConversationPanelProps {
  cad: UseCadStreamResult;
  prompt: string;
  onPromptChange: (value: string) => void;
  activeConversationId: string | null;
  onActiveConversationIdChange: (id: string | null) => void;
  onArtifactsChange?: (artifacts: ConversationArtifact[]) => void;
}

/** Gemini-ähnliche Unterhaltung: Chat-Liste, Verlauf bleibt, Artefakte wiederaufrufbar. */
export function ConversationPanel({
  cad,
  prompt,
  onPromptChange,
  activeConversationId,
  onActiveConversationIdChange,
  onArtifactsChange,
}: ConversationPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const { data: conversations = [], isLoading: listLoading } = useConversations();
  const { data: detail, isFetching: detailFetching } = useConversation(activeConversationId);
  const createConv = useCreateConversation();
  const deleteConv = useDeleteConversation();
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null);

  const isBusy = cad.status === "connecting" || cad.status === "running" || cad.status === "escalation";

  useEffect(() => {
    if (activeConversationId) {
      localStorage.setItem(ACTIVE_CONV_KEY, activeConversationId);
    }
  }, [activeConversationId]);

  // Beim ersten Load: gespeicherte oder neueste Unterhaltung aktivieren
  useEffect(() => {
    if (activeConversationId || listLoading) return;
    const stored = localStorage.getItem(ACTIVE_CONV_KEY);
    if (stored && conversations.some((c) => c.id === stored)) {
      onActiveConversationIdChange(stored);
      return;
    }
    if (conversations.length > 0) {
      onActiveConversationIdChange(conversations[0].id);
    }
  }, [activeConversationId, conversations, listLoading, onActiveConversationIdChange]);

  useEffect(() => {
    onArtifactsChange?.(detail?.artifacts ?? []);
  }, [detail?.artifacts, onArtifactsChange]);

  // Nach Run-Ende / Eskalation Conversation neu laden (Nachrichten + Artefakte)
  useEffect(() => {
    if (
      activeConversationId &&
      (cad.status === "completed" ||
        cad.status === "failed" ||
        cad.status === "cancelled" ||
        cad.status === "escalation" ||
        cad.status === "error")
    ) {
      invalidateConversation(qc, activeConversationId);
      // Inventar-Liste aktualisieren (Konzept wurde auto-gespeichert)
      qc.invalidateQueries({ queryKey: ["inventory-items"] });
    }
  }, [cad.status, activeConversationId, qc]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [detail?.messages?.length, detail?.artifacts?.length, cad.status, cad.escalation, cad.currentNode]);

  const statusHint = useMemo(() => {
    const map: Partial<Record<CadRunStatus, string>> = {
      connecting: "Verbinde…",
      running: "Agent arbeitet…",
      escalation: "Freigabe erforderlich",
      completed: "Fertig",
      failed: "Fehlgeschlagen",
      cancelled: "Pausiert",
      error: "Fehler",
    };
    return map[cad.status] ?? null;
  }, [cad.status]);

  const showConcept =
    cad.status === "escalation" && cad.escalation?.reason === "concept_approval" && cad.escalation;

  const conceptArtifacts = useMemo(() => {
    const arts = (detail?.artifacts ?? []).filter((a) => a.kind === "concept_image");
    // Während Live-Freigabe: nur die neueste Version derselben Session ausblenden
    // (ältere Konzepte bleiben mit „Ausarbeiten“ sichtbar)
    if (showConcept && cad.sessionId) {
      const sameSession = arts.filter((a) => a.cad_session_id === cad.sessionId);
      if (sameSession.length === 0) return arts;
      const newestId = [...sameSession].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]?.id;
      return arts.filter((a) => a.id !== newestId);
    }
    return arts;
  }, [detail?.artifacts, showConcept, cad.sessionId]);

  const handleElaborateConcept = async (artifactId: string) => {
    if (!activeConversationId || isBusy) return;
    await cad.elaborateConcept(activeConversationId, artifactId);
    invalidateConversation(qc, activeConversationId);
  };

  const ensureConversation = async (): Promise<string | null> => {
    if (activeConversationId) return activeConversationId;
    try {
      const created = await createConv.mutateAsync(undefined);
      onActiveConversationIdChange(created.id);
      return created.id;
    } catch {
      return null;
    }
  };

  const handleNewChat = async () => {
    if (isBusy) return;
    cad.clearRun();
    try {
      const created = await createConv.mutateAsync(undefined);
      onActiveConversationIdChange(created.id);
      onPromptChange("");
    } catch {
      /* ignore */
    }
  };

  const handleSelectChat = (id: string) => {
    if (id === activeConversationId) return;
    if (isBusy) return;
    cad.clearRun();
    onActiveConversationIdChange(id);
    onPromptChange("");
  };

  const requestDeleteChat = (id: string, title: string, event?: MouseEvent) => {
    event?.stopPropagation();
    if (isBusy && id === activeConversationId) return;
    setPendingDelete({ id, title });
  };

  const confirmDeleteChat = async () => {
    if (!pendingDelete) return;
    const { id } = pendingDelete;
    setPendingDelete(null);
    try {
      await deleteConv.mutateAsync(id);
      if (activeConversationId === id) {
        cad.clearRun();
        const next = conversations.find((c) => c.id !== id);
        onActiveConversationIdChange(next?.id ?? null);
        localStorage.removeItem(ACTIVE_CONV_KEY);
      }
    } catch {
      /* ignore */
    }
  };

  const messages = detail?.messages ?? [];

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (isBusy || prompt.trim().length < 3) return;
    const text = prompt.trim();
    const convId = await ensureConversation();
    if (!convId) return;
    onPromptChange("");
    await cad.start(text, convId);
    invalidateConversation(qc, convId);
  };

  const resumableFromHistory = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      const meta = m.meta as { resumable?: boolean; status?: string } | null;
      if (meta?.resumable && m.cad_session_id) return m.cad_session_id;
      if (meta?.status === "paused" && m.cad_session_id) return m.cad_session_id;
    }
    return null;
  }, [messages]);

  const resumeSessionId = cad.pausedSessionId || resumableFromHistory;
  const canResume =
    !isBusy &&
    !!resumeSessionId &&
    (cad.status === "cancelled" ||
      !!cad.pausedSessionId ||
      (["idle", "error", "failed"].includes(cad.status) && !!resumableFromHistory));

  const handleResume = async () => {
    if (!canResume || !resumeSessionId) return;
    const convId = activeConversationId ?? (await ensureConversation());
    await cad.resume(resumeSessionId, convId);
    if (convId) invalidateConversation(qc, convId);
  };

  const timeline = useMemo(() => {
    type Item =
      | { kind: "message"; at: string; id: string; message: (typeof messages)[number] }
      | { kind: "concept"; at: string; id: string; artifact: ConversationArtifact };
    const items: Item[] = [
      ...messages.map((m) => ({ kind: "message" as const, at: m.created_at, id: m.id, message: m })),
      ...conceptArtifacts.map((a) => ({
        kind: "concept" as const,
        at: a.created_at,
        id: a.id,
        artifact: a,
      })),
    ];
    return items.sort((a, b) => a.at.localeCompare(b.at));
  }, [messages, conceptArtifacts]);

  const liveTitle = cad.escalation?.requirements_contract?.project_title ?? "Konzept-Entwurf";
  const liveImageUrl = resolveMediaUrl(cad.escalation?.concept_image_url);

  return (
    <div className="relative flex h-full min-h-0 gap-2">
      {pendingDelete && (
        <div
          className="absolute inset-0 z-20 flex items-center justify-center bg-black/60 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-conv-title"
        >
          <div className="w-full max-w-sm rounded-lg border border-workshop-warning/70 bg-workshop-panel p-4 shadow-lg">
            <h3 id="delete-conv-title" className="text-sm font-semibold text-workshop-text">
              Unterhaltung löschen?
            </h3>
            <p className="mt-2 text-xs leading-relaxed text-workshop-muted">
              „{pendingDelete.title}“ wird unwiderruflich gelöscht – inklusive Nachrichten, Konzeptfotos
              und 3D-Artefakte dieses Chats. Inventar-Einträge bleiben erhalten.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingDelete(null)}
                className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-bg"
              >
                Abbrechen
              </button>
              <button
                type="button"
                disabled={deleteConv.isPending}
                onClick={() => void confirmDeleteChat()}
                className="rounded-md bg-workshop-danger px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
              >
                {deleteConv.isPending ? "Lösche…" : "Endgültig löschen"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Chat-Liste */}
      <aside className="flex w-40 shrink-0 flex-col gap-2 border-r border-workshop-border pr-2 sm:w-48">
        <button
          type="button"
          onClick={() => void handleNewChat()}
          disabled={isBusy || createConv.isPending}
          className="rounded-md bg-workshop-accent px-2 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
        >
          + Neue Unterhaltung
        </button>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {listLoading && <p className="text-[10px] text-workshop-muted">Lade…</p>}
          {conversations.map((c) => {
            const active = c.id === activeConversationId;
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => handleSelectChat(c.id)}
                disabled={isBusy && !active}
                className={`group flex w-full items-start gap-1 rounded-md px-2 py-1.5 text-left text-xs ${
                  active
                    ? "bg-workshop-accent/20 text-workshop-text"
                    : "text-workshop-muted hover:bg-workshop-bg hover:text-workshop-text"
                } disabled:opacity-50`}
              >
                <span className="min-w-0 flex-1 truncate" title={c.title}>
                  {c.title}
                </span>
                <span
                  role="button"
                  tabIndex={0}
                  title="Unterhaltung löschen"
                  onClick={(e) => requestDeleteChat(c.id, c.title, e)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") requestDeleteChat(c.id, c.title);
                  }}
                  className="shrink-0 text-workshop-muted opacity-0 hover:text-workshop-danger group-hover:opacity-100 focus:opacity-100"
                >
                  ×
                </span>
              </button>
            );
          })}
          {!listLoading && conversations.length === 0 && (
            <p className="text-[10px] text-workshop-muted">Noch keine Chats – sende eine Anweisung.</p>
          )}
        </div>
      </aside>

      {/* Aktiver Chat */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-xs text-workshop-muted">
            {detail?.title ?? "Unterhaltung"}
            {statusHint ? ` · ${statusHint}` : ""}
            {detailFetching ? " · …" : ""}
          </span>
          <div className="flex shrink-0 items-center gap-2">
            {activeConversationId && (
              <button
                type="button"
                disabled={isBusy || deleteConv.isPending}
                onClick={() =>
                  requestDeleteChat(activeConversationId, detail?.title ?? "Unterhaltung")
                }
                className="rounded border border-workshop-border px-2 py-0.5 text-[10px] font-semibold text-workshop-muted hover:border-workshop-danger hover:text-workshop-danger disabled:opacity-40"
                title="Diese Unterhaltung löschen"
              >
                Löschen
              </button>
            )}
            <StatusBadge status={cad.status} currentNode={cad.currentNode} />
          </div>
        </div>

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 space-y-3 overflow-y-auto rounded-md border border-workshop-border bg-black/20 p-3"
        >
          {messages.length === 0 && !isBusy && conceptArtifacts.length === 0 && (
            <p className="text-xs text-workshop-muted">
              Beschreibe dein Bauteil unten – der Verlauf bleibt erhalten, und du kannst jederzeit
              nachsteuern. Frühere Chats links wieder aktivieren.
            </p>
          )}

          {timeline.map((item) => {
            if (item.kind === "message") {
              const m = item.message;
              return (
                <div
                  key={m.id}
                  className={
                    m.role === "user"
                      ? "ml-8 rounded-lg bg-workshop-accent/15 px-3 py-2 text-sm text-workshop-text"
                      : "mr-8 rounded-lg border border-workshop-border bg-workshop-panel px-3 py-2 text-xs text-workshop-text"
                  }
                >
                  {m.content}
                </div>
              );
            }
            const art = item.artifact;
            const meta = (art.meta ?? {}) as {
              requirements_contract?: EscalationPayload["requirements_contract"];
              project_title?: string;
              ausarbeiten?: boolean;
            };
            return (
              <div key={art.id} className="mr-4">
                <ConceptImageCard
                  title={meta.project_title || art.label || detail?.title || "Konzept-Foto"}
                  imageUrl={resolveMediaUrl(art.url)}
                  parts={meta.requirements_contract?.parts}
                  onElaborate={
                    meta.ausarbeiten === false ? undefined : () => void handleElaborateConcept(art.id)
                  }
                  elaborating={isBusy}
                />
              </div>
            );
          })}

          {isBusy && cad.status !== "escalation" && (
            <div className="mr-8 rounded-lg border border-dashed border-workshop-border px-3 py-2 text-xs text-workshop-muted">
              {cad.currentNode ? `Arbeitet: ${cad.currentNode}…` : "Agent arbeitet…"}
              {cad.totalParts > 0 && (
                <span>
                  {" "}
                  · Teil {Math.min(cad.currentPartIndex + 1, cad.totalParts)}/{cad.totalParts}
                  {cad.currentPartName ? ` (${cad.currentPartName})` : ""}
                </span>
              )}
            </div>
          )}

          {showConcept && (
            <div className="mr-4">
              <ConceptImageCard
                title={liveTitle}
                imageUrl={liveImageUrl}
                parts={cad.escalation!.requirements_contract?.parts}
                interactive
                onDecision={(decision) => cad.resolveEscalation(decision)}
              />
            </div>
          )}

          {cad.errorMessage && <p className="text-xs text-workshop-danger">{cad.errorMessage}</p>}
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} className="flex shrink-0 flex-col gap-2 border-t border-workshop-border pt-3">
          <ReferenceUpload onAttach={(text) => onPromptChange(prompt ? `${prompt}\n${text}` : text)} />
          <textarea
            rows={3}
            value={prompt}
            onChange={(event) => onPromptChange(event.target.value)}
            placeholder='Folgeanweisung oder z. B. "Eckschrank für 1. OG…"'
            disabled={isBusy}
            className="resize-none rounded-md border border-workshop-border bg-workshop-bg p-3 text-sm text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none disabled:opacity-60"
          />
          <div className="flex justify-end gap-2">
            {canResume && (
              <button
                type="button"
                onClick={() => void handleResume()}
                className="rounded-md bg-workshop-accent px-4 py-2 text-sm font-semibold text-workshop-bg hover:opacity-90"
                title="Pausierten Workflow lückenlos fortsetzen (Konzept bleibt erhalten)"
              >
                Fortsetzen
              </button>
            )}
            {isBusy && (
              <button
                type="button"
                onClick={() => void cad.cancel()}
                className="rounded-md border border-workshop-danger px-4 py-2 text-sm font-semibold text-workshop-danger hover:bg-workshop-danger/10"
              >
                Abbrechen
              </button>
            )}
            <button
              type="submit"
              disabled={isBusy || prompt.trim().length < 3}
              className={`rounded-md px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${
                canResume
                  ? "border border-workshop-border text-workshop-text hover:bg-workshop-bg"
                  : "bg-workshop-accent text-workshop-bg"
              }`}
            >
              {isBusy ? "Läuft…" : "Senden"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
