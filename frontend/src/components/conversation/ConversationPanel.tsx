import { useEffect, useMemo, useRef, useState, type FormEvent, type MouseEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api, apiBaseUrl } from "../../api/client";
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
import type { ReferenceMediaPreview } from "../modelViewer/ModelViewer";
import { ReferenceUpload } from "../commandCenter/ReferenceUpload";
import { ClarificationPanel, RequirementsConfirmPanel } from "../agentTrace/EscalationDialog";
import { assetFileUrl } from "../inventory/AssetPreview";

const ACTIVE_CONV_KEY = "werkstatt.activeConversationId";

function resolveMediaUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.startsWith("http") ? url : `${apiBaseUrl()}${url}`;
}

type ContractParts = NonNullable<EscalationPayload["requirements_contract"]>["parts"];

const COMPLEXITY_LABEL: Record<string, string> = {
  low: "niedrig",
  medium: "mittel",
  high: "hoch",
};

const ROSTER_LABEL: Record<string, string> = {
  flexible_specialist: "Flexible",
  interior_architect: "Innenarchitekt",
  vv_manager: "V&V",
  concept_critic: "Critic",
  fertigung_specialist: "Fertigung",
};

function complexityLabel(level: string | null | undefined): string {
  if (!level) return "";
  return COMPLEXITY_LABEL[level] ?? level;
}

function rosterLabel(agentId: string): string {
  return ROSTER_LABEL[agentId] ?? agentId;
}

function gradeChipClass(grade?: number | null): string {
  if (grade == null || Number.isNaN(grade)) {
    return "border-workshop-border text-workshop-muted";
  }
  if (grade <= 2) {
    return "border-workshop-success/50 bg-workshop-success/10 text-workshop-success";
  }
  if (grade <= 4) {
    return "border-workshop-border bg-workshop-panel/80 text-workshop-text";
  }
  return "border-workshop-danger/50 bg-workshop-danger/10 text-workshop-danger";
}

function PanelFeedbackDetail({ grades }: { grades: NonNullable<EscalationPayload["concept_panel_grades"]> }) {
  return (
    <div className="space-y-2">
      {grades.map((g, i) => {
        const issues = (g.issues ?? []).filter(Boolean);
        const strengths = (g.strengths ?? []).filter(Boolean);
        const improvement = (g.improvement ?? "").trim();
        const verdict = (g.verdict ?? "").trim();
        return (
          <div
            key={`${g.agent_id ?? "a"}-${i}`}
            className="rounded border border-workshop-border/80 bg-workshop-panel/40 px-2 py-1.5 text-[11px] leading-snug"
          >
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${gradeChipClass(g.grade)}`}
              >
                {g.grade ?? "–"}
              </span>
              <span className="font-semibold text-workshop-text">{rosterLabel(g.agent_id ?? "Agent")}</span>
            </div>
            {verdict && <p className="whitespace-pre-wrap text-workshop-text">{verdict}</p>}
            {strengths.length > 0 && (
              <ul className="mt-1 list-inside list-disc text-workshop-muted">
                {strengths.map((item) => (
                  <li key={item} className="whitespace-pre-wrap">
                    {item}
                  </li>
                ))}
              </ul>
            )}
            {issues.length > 0 && (
              <ul className="mt-1 list-inside list-disc text-workshop-warning/90">
                {issues.map((item) => (
                  <li key={item} className="whitespace-pre-wrap">
                    {item}
                  </li>
                ))}
              </ul>
            )}
            {improvement && (
              <p className="mt-1 whitespace-pre-wrap text-workshop-accent">
                <span className="font-semibold">Verbesserung:</span> {improvement}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ConceptImageCard({
  title,
  imageUrl,
  imageUrls,
  sessionId,
  referenceAssetIds,
  coherenceCritique,
  panelGrades,
  panelAverage,
  panelReverted,
  panelRound,
  complexity,
  roster,
  complexityReasons,
  parts,
  interactive,
  onDecision,
  onElaborate,
  elaborating,
}: {
  title: string;
  imageUrl: string | null;
  imageUrls?: Array<{ url: string; label?: string; kind?: string }> | null;
  /** CAD-Session – Fallback, Galerie von Disk nachladen */
  sessionId?: string | null;
  referenceAssetIds?: string[] | null;
  coherenceCritique?: EscalationPayload["coherence_critique"];
  panelGrades?: EscalationPayload["concept_panel_grades"];
  panelAverage?: number | null;
  panelReverted?: boolean;
  panelRound?: number | null;
  complexity?: string | null;
  roster?: string[] | null;
  complexityReasons?: string[] | null;
  parts?: ContractParts;
  interactive?: boolean;
  onDecision?: (decision: ConceptDecision) => void;
  /** Historisches Konzept: Ausarbeitung neu starten */
  onElaborate?: () => void;
  elaborating?: boolean;
}) {
  const [mode, setMode] = useState<"view" | "revise">("view");
  const [feedback, setFeedback] = useState("");
  const [userGrade, setUserGrade] = useState<number | null>(null);
  const [partsOpen, setPartsOpen] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(true);
  const [activeIdx, setActiveIdx] = useState(0);
  const [fetchedUrls, setFetchedUrls] = useState<Array<{
    url: string;
    label?: string;
    kind?: string;
  }> | null>(null);
  const partList = parts ?? [];

  useEffect(() => {
    const sid = sessionId?.trim();
    const have = (imageUrls?.length ?? 0) > 1;
    if (!sid || have) {
      setFetchedUrls(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const data = await api.get<{
          items: Array<{ url: string; label?: string; kind?: string }>;
        }>(`/api/v1/cad/concept-image/${encodeURIComponent(sid)}/gallery`);
        if (!cancelled && (data.items?.length ?? 0) > 1) {
          setFetchedUrls(data.items);
        }
      } catch {
        if (!cancelled) setFetchedUrls(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, imageUrls]);

  const gallery = useMemo(() => {
    const source =
      (imageUrls?.length ?? 0) > 1
        ? imageUrls
        : (fetchedUrls?.length ?? 0) > 1
          ? fetchedUrls
          : imageUrls;
    const fromList = (source ?? [])
      .map((v) => ({
        url: resolveMediaUrl(v.url) || "",
        label: v.label || v.kind || "Ansicht",
        kind: v.kind,
      }))
      .filter((v) => v.url);
    if (fromList.length > 0) {
      // Übersicht zuerst, dann Grundriss, dann Details
      const rank = (k?: string) =>
        k === "overview" ? 0 : k === "floorplan" ? 1 : 2;
      return [...fromList].sort((a, b) => rank(a.kind) - rank(b.kind));
    }
    const single = resolveMediaUrl(imageUrl);
    return single ? [{ url: single, label: "Raumsituation (maßgeblich)", kind: "overview" }] : [];
  }, [imageUrl, imageUrls, fetchedUrls]);

  useEffect(() => {
    const overviewIdx = gallery.findIndex((g) => g.kind === "overview");
    setActiveIdx(overviewIdx >= 0 ? overviewIdx : 0);
  }, [gallery.length, gallery[0]?.url]);

  const active = gallery[Math.min(activeIdx, Math.max(gallery.length - 1, 0))] ?? null;

  const kindBadge = (kind?: string) => {
    if (kind === "overview") return "Maßgeblich";
    if (kind === "floorplan") return "Grundriss";
    if (kind === "part") return "Detail";
    return "Ansicht";
  };

  return (
    <div className="rounded-lg border border-workshop-accent/60 bg-workshop-bg/80 p-3">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="text-xs font-semibold text-workshop-accent">
          {interactive
            ? panelRound
              ? `Konzept Runde ${panelRound} – deine Entscheidung: ${title}`
              : `Konzept zur Entscheidung: ${title}`
            : panelRound
              ? `Konzept Runde ${panelRound}: ${title}`
              : `Konzept: ${title}`}
        </div>
        <span
          className="shrink-0 rounded border border-workshop-border px-1.5 py-0.5 text-[10px] text-workshop-muted"
          title="Automatisch in Inventar-DB gespeichert"
        >
          KI-Generiert
        </span>
      </div>

      {gallery.length > 1 && (
        <p className="mb-2 text-[11px] leading-snug text-workshop-muted">
          Freigabe gilt für das <span className="text-workshop-text">Gesamt-Konzept</span> (
          Ansicht „Maßgeblich“). Weitere Bilder sind Zusatzansichten desselben Entwurfs – keine
          Alternativ-Varianten zum Auswählen.
        </p>
      )}

      {(panelGrades?.length ?? 0) > 0 && (
        <div className="mb-3 rounded border border-workshop-border/80 bg-workshop-panel/50 px-2 py-1.5">
          <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-workshop-muted">
            <span className="font-semibold uppercase tracking-wide text-workshop-accent">Agenten-Noten</span>
            {panelAverage != null ? <span>Schnitt {panelAverage}</span> : null}
            {panelRound ? <span>Runde {panelRound}</span> : null}
            {panelReverted ? <span>vorherige Fassung</span> : null}
            {complexity ? <span>Komplexität {complexityLabel(complexity)}</span> : null}
          </div>
          <div className="mb-2 flex flex-wrap gap-1.5">
            {panelGrades!.map((g, i) => (
              <span
                key={`${g.agent_id ?? "a"}-${i}`}
                className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium ${gradeChipClass(g.grade)}`}
              >
                <span className="tabular-nums">{g.grade ?? "–"}</span>
                <span>{rosterLabel(g.agent_id ?? "Agent")}</span>
              </span>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setFeedbackOpen((v) => !v)}
            className="text-[10px] font-semibold text-workshop-muted hover:text-workshop-text"
          >
            {feedbackOpen ? "▾" : "▸"} Detailliertes Agenten-Feedback
          </button>
          {feedbackOpen && (
            <div className="mt-2 max-h-56 overflow-y-auto">
              <PanelFeedbackDetail grades={panelGrades!} />
            </div>
          )}
        </div>
      )}

      {(referenceAssetIds?.length ?? 0) > 0 && (
        <div className="mb-2 rounded border border-workshop-border/80 bg-workshop-panel/60 px-2 py-1.5">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-workshop-accent">
            Deine Referenzfotos an die KI ({referenceAssetIds!.length})
          </div>
          <div className="flex gap-1.5 overflow-x-auto">
            {referenceAssetIds!.slice(0, 8).map((id) => (
              <a
                key={id}
                href={assetFileUrl(id)}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 overflow-hidden rounded border border-workshop-border"
                title={id}
              >
                <img src={assetFileUrl(id)} alt="Referenz" className="h-12 w-12 object-cover" loading="lazy" />
              </a>
            ))}
          </div>
        </div>
      )}

      {coherenceCritique &&
        (coherenceCritique.summary ||
          (coherenceCritique.contradictions?.length ?? 0) > 0 ||
          (coherenceCritique.logic_gaps?.length ?? 0) > 0 ||
          (coherenceCritique.missing_information?.length ?? 0) > 0 ||
          (coherenceCritique.concept_issues?.length ?? 0) > 0) && (
          <div className="mb-3 rounded border border-workshop-warning/50 bg-workshop-warning/10 px-2.5 py-2">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-workshop-warning">
              Prüfung Bild ↔ Text ↔ Konzept
              {coherenceCritique.severity ? ` · ${coherenceCritique.severity}` : ""}
              {coherenceCritique.verdict ? ` · ${coherenceCritique.verdict}` : ""}
            </div>
            {coherenceCritique.summary && (
              <p className="mb-1.5 text-[11px] leading-snug text-workshop-text">{coherenceCritique.summary}</p>
            )}
            {(
              [
                ["Widersprüche", coherenceCritique.contradictions],
                ["Logiklücken", coherenceCritique.logic_gaps],
                ["Fehlende Infos", coherenceCritique.missing_information],
                ["Konzept-Mängel", coherenceCritique.concept_issues],
                ["Zu klären", coherenceCritique.must_ask_user],
              ] as const
            ).map(([label, items]) =>
              items && items.length > 0 ? (
                <div key={label} className="mb-1">
                  <div className="text-[10px] font-semibold text-workshop-muted">{label}</div>
                  <ul className="list-inside list-disc text-[11px] text-workshop-text">
                    {items.slice(0, 6).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null,
            )}
          </div>
        )}

      {active ? (
        <div className="mb-3">
          <a href={active.url} target="_blank" rel="noreferrer" className="block">
            <img
              src={active.url}
              alt={active.label}
              className="max-h-72 w-full rounded-md border border-workshop-border object-cover"
            />
          </a>
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <span className="text-[11px] text-workshop-muted">
              <span className="mr-1 rounded border border-workshop-accent/50 px-1 py-0.5 text-[10px] text-workshop-accent">
                {kindBadge(active.kind)}
              </span>
              {active.label}
              {gallery.length > 1 ? ` · ${activeIdx + 1}/${gallery.length}` : ""}
            </span>
            {gallery.length > 1 && (
              <div className="flex gap-1">
                <button
                  type="button"
                  className="rounded border border-workshop-border px-2 py-0.5 text-[11px] text-workshop-muted hover:text-workshop-text"
                  onClick={() => setActiveIdx((i) => (i - 1 + gallery.length) % gallery.length)}
                >
                  ←
                </button>
                <button
                  type="button"
                  className="rounded border border-workshop-border px-2 py-0.5 text-[11px] text-workshop-muted hover:text-workshop-text"
                  onClick={() => setActiveIdx((i) => (i + 1) % gallery.length)}
                >
                  →
                </button>
              </div>
            )}
          </div>
          {gallery.length > 1 && (
            <div className="mt-2 flex gap-1.5 overflow-x-auto pb-1">
              {gallery.map((g, idx) => (
                <button
                  key={`${g.url}-${idx}`}
                  type="button"
                  onClick={() => setActiveIdx(idx)}
                  className={`relative shrink-0 overflow-hidden rounded border ${
                    idx === activeIdx ? "border-workshop-accent" : "border-workshop-border opacity-70"
                  }`}
                  title={`${kindBadge(g.kind)}: ${g.label}`}
                >
                  <img src={g.url} alt={g.label} className="h-12 w-16 object-cover" loading="lazy" />
                  <span className="absolute bottom-0 left-0 right-0 bg-black/65 px-0.5 text-center text-[8px] leading-tight text-white">
                    {kindBadge(g.kind)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="mb-3 flex max-h-48 min-h-32 items-center justify-center rounded-md border border-dashed border-workshop-border bg-black/20 p-4 text-center text-xs text-workshop-muted">
          Kein Raumfoto verfügbar – bitte einen{" "}
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
              <div>
                <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-workshop-muted">
                  Deine Bewertung (optional)
                </div>
                <div className="flex flex-wrap gap-1">
                  {[1, 2, 3, 4, 5, 6].map((g) => (
                    <button
                      key={g}
                      type="button"
                      onClick={() => setUserGrade(userGrade === g ? null : g)}
                      className={`rounded border px-2 py-1 text-[10px] font-semibold tabular-nums ${
                        userGrade === g
                          ? "border-workshop-accent bg-workshop-accent/20 text-workshop-accent"
                          : "border-workshop-border text-workshop-muted hover:text-workshop-text"
                      }`}
                    >
                      {g}
                    </button>
                  ))}
                </div>
              </div>
              <textarea
                value={feedback}
                onChange={(event) => setFeedback(event.target.value)}
                placeholder="Was soll der Concept Builder in der nächsten Runde anders machen? (optional – Jury-Feedback wird automatisch mitgegeben)"
                rows={3}
                className="w-full resize-none rounded-md border border-workshop-border bg-workshop-panel p-2 text-xs"
              />
              <div className="flex justify-end gap-2">
                <button type="button" onClick={() => setMode("view")} className="text-xs text-workshop-muted hover:underline">
                  Zurück
                </button>
                <button
                  type="button"
                  onClick={() =>
                    onDecision({
                      decision: "next_round",
                      feedback: feedback.trim() || undefined,
                      user_grade: userGrade ?? undefined,
                    })
                  }
                  className="rounded-md bg-workshop-warning px-3 py-1.5 text-xs font-semibold text-workshop-bg"
                >
                  Nächste Runde starten
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-[11px] leading-snug text-workshop-muted">
                Du entscheidest: Konzept freigeben und ausarbeiten lassen, oder eine weitere Runde mit
                Agenten-Feedback (und optional deiner eigenen Note) starten.
              </p>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setMode("revise")}
                  className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-panel"
                >
                  Nächste Runde
                </button>
                <button
                  type="button"
                  onClick={() => onDecision({ decision: "approve" })}
                  className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg"
                  title="Gibt das Gesamt-Konzept frei (Maßgeblich = Raumsituation), nicht einzelne Thumbnails"
                >
                  Konzept freigeben
                </button>
              </div>
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
  onReferencePreviewChange?: (preview: ReferenceMediaPreview | null) => void;
}

/** Gemini-ähnliche Unterhaltung: Chat-Liste, Verlauf bleibt, Artefakte wiederaufrufbar. */
export function ConversationPanel({
  cad,
  prompt,
  onPromptChange,
  activeConversationId,
  onActiveConversationIdChange,
  onArtifactsChange,
  onReferencePreviewChange,
}: ConversationPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const { data: conversations = [], isLoading: listLoading } = useConversations();
  const { data: detail, isFetching: detailFetching } = useConversation(activeConversationId);
  const createConv = useCreateConversation();
  const deleteConv = useDeleteConversation();
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null);
  const [attachContext, setAttachContext] = useState("");
  const [attachClearToken, setAttachClearToken] = useState(0);

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

  // Nach Run-Ende / Eskalation / Entscheidung Conversation neu laden
  useEffect(() => {
    if (
      activeConversationId &&
      (cad.status === "completed" ||
        cad.status === "failed" ||
        cad.status === "cancelled" ||
        cad.status === "escalation" ||
        cad.status === "running" ||
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

  const showClarification =
    cad.status === "escalation" &&
    cad.escalation &&
    (cad.escalation.reason === "concept_clarification" ||
      cad.escalation.reason === "requirements_question");

  const showRequirementsConfirm =
    cad.status === "escalation" &&
    cad.escalation &&
    (cad.escalation.reason === "requirements_confirm" ||
      cad.escalation.reason === "requirements_approval");

  const conceptArtifacts = useMemo(() => {
    const arts = (detail?.artifacts ?? []).filter((a) => a.kind === "concept_image");
    if (!showConcept || !cad.sessionId) return arts;
    const liveRound = cad.escalation?.concept_panel_round;
    const sameSession = arts.filter((a) => a.cad_session_id === cad.sessionId);
    if (sameSession.length === 0) return arts;
    const duplicate = [...sameSession]
      .filter((a) => {
        const meta = a.meta ?? {};
        return liveRound == null || meta.concept_panel_round === liveRound;
      })
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    if (!duplicate) return arts;
    return arts.filter((a) => a.id !== duplicate.id);
  }, [detail?.artifacts, showConcept, cad.sessionId, cad.escalation?.concept_panel_round]);

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
    let text = [prompt.trim(), attachContext.trim()].filter(Boolean).join("\n\n");
    // Client-Schutz: gleiche Grenze wie Backend (50k)
    if (text.length > 50_000) {
      text = text.slice(0, 50_000);
    }
    const convId = await ensureConversation();
    if (!convId) return;

    // 1) User-Nachricht zuerst dauerhaft speichern – sonst geht sie bei Fehlern verloren
    try {
      await api.post(`/api/v1/conversations/${convId}/messages`, {
        role: "user",
        content: text,
      });
    } catch (err) {
      window.alert(
        err instanceof Error
          ? `Nachricht konnte nicht gespeichert werden: ${err.message}`
          : "Nachricht konnte nicht gespeichert werden.",
      );
      return;
    }

    onPromptChange("");
    setAttachContext("");
    setAttachClearToken((n) => n + 1);
    invalidateConversation(qc, convId);

    // 2) CAD starten ohne zweite User-Message in der DB
    await cad.start(text, convId, { persistUserMessage: false });
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
  const liveImageUrls = cad.escalation?.concept_image_urls ?? null;
  const liveRefIds = cad.escalation?.reference_asset_ids ?? null;
  const liveCritique = cad.escalation?.coherence_critique ?? null;

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
              concept_image_urls?: EscalationPayload["concept_image_urls"];
              concept_panel_grades?: EscalationPayload["concept_panel_grades"];
              concept_panel_average?: number | null;
              concept_panel_round?: number | null;
              concept_panel_reverted?: boolean;
              session_id?: string;
            };
            return (
              <div key={art.id} className="mr-4">
                <ConceptImageCard
                  title={meta.project_title || art.label || detail?.title || "Konzept-Foto"}
                  imageUrl={resolveMediaUrl(art.url)}
                  imageUrls={meta.concept_image_urls}
                  sessionId={meta.session_id || art.cad_session_id}
                  panelGrades={meta.concept_panel_grades}
                  panelAverage={meta.concept_panel_average}
                  panelRound={meta.concept_panel_round}
                  panelReverted={meta.concept_panel_reverted}
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
                imageUrls={liveImageUrls}
                sessionId={cad.escalation?.session_id ?? cad.sessionId}
                referenceAssetIds={liveRefIds}
                coherenceCritique={liveCritique}
                panelGrades={cad.escalation?.concept_panel_grades}
                panelAverage={cad.escalation?.concept_panel_average}
                panelReverted={cad.escalation?.concept_panel_reverted}
                panelRound={cad.escalation?.concept_panel_round}
                complexity={cad.escalation?.concept_complexity}
                roster={cad.escalation?.concept_roster}
                complexityReasons={cad.escalation?.concept_complexity_reasons}
                parts={cad.escalation!.requirements_contract?.parts}
                interactive
                onDecision={(decision) => {
                  cad.resolveEscalation(decision);
                  // Chat neu laden – Backend speichert Anpassung/Freigabe in der Conversation
                  if (activeConversationId) {
                    window.setTimeout(() => invalidateConversation(qc, activeConversationId), 400);
                    window.setTimeout(() => invalidateConversation(qc, activeConversationId), 2000);
                  }
                }}
              />
            </div>
          )}

          {showClarification && cad.escalation && (
            <div className="mr-4">
              <ClarificationPanel
                escalation={cad.escalation}
                variant="inline"
                onDecision={(decision) => {
                  cad.resolveEscalation(decision);
                  if (activeConversationId) {
                    window.setTimeout(() => invalidateConversation(qc, activeConversationId), 400);
                  }
                }}
              />
            </div>
          )}

          {showRequirementsConfirm && cad.escalation && (
            <div className="mr-4">
              <RequirementsConfirmPanel
                escalation={cad.escalation}
                variant="inline"
                onDecision={(decision) => {
                  cad.resolveEscalation(decision);
                  if (activeConversationId) {
                    window.setTimeout(() => invalidateConversation(qc, activeConversationId), 400);
                    window.setTimeout(() => invalidateConversation(qc, activeConversationId), 2000);
                  }
                }}
              />
            </div>
          )}

          {cad.errorMessage && <p className="text-xs text-workshop-danger">{cad.errorMessage}</p>}
        </div>

        <form
          onSubmit={(e) => void handleSubmit(e)}
          className="flex max-h-[46%] min-h-0 shrink-0 flex-col gap-2 border-t border-workshop-border pt-3"
        >
          <div className="min-h-0 overflow-y-auto">
            <ReferenceUpload
              onContextChange={setAttachContext}
              onPreviewChange={onReferencePreviewChange}
              clearToken={attachClearToken}
            />
          </div>
          <textarea
            rows={3}
            value={prompt}
            onChange={(event) => onPromptChange(event.target.value)}
            placeholder='Folgeanweisung oder z. B. "Eckschrank für 1. OG…"'
            disabled={isBusy}
            className="shrink-0 resize-none rounded-md border border-workshop-border bg-workshop-bg p-3 text-sm text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none disabled:opacity-60"
          />
          {attachContext.trim() && (
            <p className="shrink-0 text-[10px] text-workshop-muted">
              Inventar-/Datei-Beschreibungen werden beim Senden mitgeschickt, erscheinen aber nicht im Textfeld.
            </p>
          )}
          <div className="flex shrink-0 justify-end gap-2">
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
