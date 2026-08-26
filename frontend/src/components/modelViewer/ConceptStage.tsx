import { useEffect, useMemo, useState } from "react";

import { apiBaseUrl } from "../../api/client";
import type { AgentNodeName, AgentTranscriptEntry, EscalationPayload } from "../../api/types";
import type { UseCadStreamResult } from "../../api/useCadStream";

const ROSTER_LABEL: Record<string, string> = {
  supervisor: "Supervisor",
  flexible_specialist: "Flexible",
  interior_architect: "Innenarchitekt",
  vv_manager: "V&V",
  concept_builder: "Builder",
  concept_critic: "Critic",
  fertigung_specialist: "Fertigung",
  concept_panel_reviewer: "Jury",
  human_escalation: "Freigabe",
};

const STAGE_EVENTS = new Set([
  "concept_draft",
  "panel_round_start",
  "panel_grade",
  "panel_optimize",
  "panel_revert_revise",
  "panel_revert_present",
  "panel_present",
  "complexity_roster",
]);

function resolveUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.startsWith("http") ? url : `${apiBaseUrl()}${url}`;
}

function agentLabel(id: string | null | undefined): string {
  if (!id) return "Agent";
  return ROSTER_LABEL[id] ?? id;
}

function gradeClass(grade?: number | null): string {
  if (grade == null || Number.isNaN(grade)) return "border-workshop-border text-workshop-muted";
  if (grade <= 2) return "border-workshop-success/50 bg-workshop-success/10 text-workshop-success";
  if (grade <= 4) return "border-workshop-border bg-workshop-bg text-workshop-text";
  return "border-workshop-danger/50 bg-workshop-danger/10 text-workshop-danger";
}

function activityLine(cad: UseCadStreamResult, reviewerId: string | null, awaiting: boolean): string {
  if (cad.status === "escalation") {
    const reason = cad.escalation?.reason;
    if (reason === "concept_approval") return "Konzept liegt zur Freigabe bereit.";
    if (reason === "concept_clarification") return "Offene Punkte – Klärung im Chat.";
    if (reason === "requirements_question") return "V&V stellt Klärungsfragen im Chat.";
    if (reason === "requirements_confirm") return "Anforderungsliste wartet auf Bestätigung.";
    return "Wartet auf deine Entscheidung.";
  }
  if (awaiting || cad.currentNode === "concept_builder") {
    return "Concept Builder passt den Entwurf an die Noten an…";
  }
  if (cad.currentNode === "concept_panel_reviewer") {
    return `${agentLabel(reviewerId)} bewertet gerade…`;
  }
  if (cad.currentNode === "interior_architect") return "Innenarchitekt plant Raum und Ansichten…";
  if (cad.currentNode === "flexible_specialist") return "Flexible Specialist berät zum Auftrag…";
  if (cad.currentNode === "vv_manager") return "V&V prüft Anforderungen…";
  if (cad.currentNode === "concept_critic") return "Concept Critic prüft den Entwurf…";
  if (cad.currentNode) return `${agentLabel(cad.currentNode)} arbeitet…`;
  return "Konzeptphase läuft.";
}

type GradeRow = NonNullable<EscalationPayload["concept_panel_grades"]>[number];
type ImageItem = { url: string; label?: string; kind?: string };

function gradeLabel(grade?: number | null): string {
  if (grade == null || Number.isNaN(grade)) return "";
  const labels: Record<number, string> = {
    1: "sehr gut",
    2: "gut",
    3: "befriedigend",
    4: "ausreichend",
    5: "mangelhaft",
    6: "ungenügend",
  };
  return labels[grade] ?? "";
}

function FeedbackCard({
  grade,
  selected,
  onSelect,
}: {
  grade: GradeRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const agentId = String(grade.agent_id || "");
  const issues = (grade.issues ?? []).filter(Boolean);
  const strengths = (grade.strengths ?? []).filter(Boolean);
  const improvement = (grade.improvement ?? "").trim();
  const verdict = (grade.verdict ?? "").trim();

  return (
    <article
      id={`concept-feedback-${agentId}`}
      onClick={onSelect}
      className={`cursor-pointer rounded-md border px-2.5 py-2 text-[11px] leading-snug transition ${
        selected
          ? "border-workshop-accent bg-workshop-accent/10"
          : "border-workshop-border/80 bg-workshop-panel/40 hover:border-workshop-border"
      }`}
    >
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${gradeClass(grade.grade)}`}
        >
          {grade.grade ?? "–"}
        </span>
        <span className="font-semibold text-workshop-text">{agentLabel(agentId)}</span>
        {grade.grade != null && (
          <span className="text-[10px] text-workshop-muted">({gradeLabel(grade.grade)})</span>
        )}
      </div>

      {verdict && <p className="whitespace-pre-wrap text-workshop-text">{verdict}</p>}

      {strengths.length > 0 && (
        <div className="mt-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-workshop-success">Stärken</div>
          <ul className="mt-0.5 list-inside list-disc text-workshop-muted">
            {strengths.map((item, i) => (
              <li key={i} className="whitespace-pre-wrap">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {issues.length > 0 && (
        <div className="mt-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-workshop-warning">Probleme</div>
          <ul className="mt-0.5 list-inside list-disc text-workshop-muted">
            {issues.map((item, i) => (
              <li key={i} className="whitespace-pre-wrap">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {improvement && (
        <div className="mt-1.5 rounded border border-workshop-accent/30 bg-workshop-accent/5 px-2 py-1">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-workshop-accent">Verbesserung</div>
          <p className="mt-0.5 whitespace-pre-wrap text-workshop-text">{improvement}</p>
        </div>
      )}
    </article>
  );
}

/** Live-Bühne: vorläufige Konzeptbilder + Jury-Noten im Model-Viewer-Bereich. */
export function ConceptStage({ cad }: { cad: UseCadStreamResult }) {
  const [activeIdx, setActiveIdx] = useState(0);
  const [selectedFeedbackId, setSelectedFeedbackId] = useState<string | null>(null);

  const images = useMemo(() => {
    const raw =
      (cad.conceptImageUrls?.length ? cad.conceptImageUrls : null) ??
      (cad.escalation?.concept_image_urls?.length ? cad.escalation.concept_image_urls : null) ??
      [];
    const mapped: ImageItem[] = raw
      .map((v) => ({
        url: resolveUrl(v.url) || "",
        label: v.label || v.kind || "Ansicht",
        kind: v.kind,
      }))
      .filter((v) => v.url);
    if (mapped.length === 0) {
      const one = resolveUrl(cad.conceptImageUrl || cad.escalation?.concept_image_url);
      if (one) mapped.push({ url: one, label: "Raumsituation", kind: "overview" });
    }
    const rank = (k?: string) => (k === "overview" ? 0 : k === "floorplan" ? 1 : 2);
    return [...mapped].sort((a, b) => rank(a.kind) - rank(b.kind));
  }, [cad.conceptImageUrl, cad.conceptImageUrls, cad.escalation]);

  const sketchSvg = cad.conceptSketchSvg ?? cad.escalation?.concept_sketch_svg ?? null;

  useEffect(() => {
    const overviewIdx = images.findIndex((g) => g.kind === "overview");
    setActiveIdx(overviewIdx >= 0 ? overviewIdx : 0);
  }, [images.length, images[0]?.url]);

  const grades: GradeRow[] =
    cad.conceptPanelGrades.length > 0
      ? cad.conceptPanelGrades
      : (cad.escalation?.concept_panel_grades ?? []);
  const roster =
    cad.conceptRoster.length > 0
      ? cad.conceptRoster
      : grades.map((g) => String(g.agent_id || "")).filter(Boolean);
  const queue = new Set(cad.conceptPanelQueue);
  const reviewerId = cad.panelReviewerId;
  const gradeByAgent = new Map(grades.map((g) => [String(g.agent_id || ""), g]));
  const round = cad.conceptPanelRound;
  const awaiting = cad.conceptPanelAwaitingRebuild;
  const active = images[Math.min(activeIdx, Math.max(images.length - 1, 0))] ?? null;

  const liveAgents = useMemo(() => {
    const ids = new Set<string>(roster);
    if (cad.currentNode && cad.currentNode !== "supervisor") ids.add(cad.currentNode);
    if (reviewerId) ids.add(reviewerId);
    const order = [
      "vv_manager",
      "flexible_specialist",
      "interior_architect",
      "concept_builder",
      "concept_critic",
      "fertigung_specialist",
      "concept_panel_reviewer",
    ];
    return [...ids].sort((a, b) => {
      const ai = order.indexOf(a);
      const bi = order.indexOf(b);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
  }, [cad.currentNode, reviewerId, roster]);

  const timeline = useMemo(() => {
    return (cad.agentTranscript as AgentTranscriptEntry[])
      .filter((e) => STAGE_EVENTS.has(e.event))
      .slice(-12)
      .reverse();
  }, [cad.agentTranscript]);

  const sortedFeedback = useMemo(() => {
    const order = [
      "vv_manager",
      "flexible_specialist",
      "interior_architect",
      "concept_critic",
      "fertigung_specialist",
      "concept_builder",
    ];
    return [...grades].sort((a, b) => {
      const ai = order.indexOf(String(a.agent_id || ""));
      const bi = order.indexOf(String(b.agent_id || ""));
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
  }, [grades]);

  const focusFeedback = (agentId: string) => {
    setSelectedFeedbackId(agentId);
    requestAnimationFrame(() => {
      document.getElementById(`concept-feedback-${agentId}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-xs font-semibold text-workshop-accent">
            {cad.conceptTitle || "Konzept-Bühne"}
          </div>
          <p className="text-[11px] text-workshop-muted">{activityLine(cad, reviewerId, awaiting)}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 text-[10px] text-workshop-muted">
          {round > 0 && <span className="rounded border border-workshop-border px-1.5 py-0.5">Runde {round}/4</span>}
          {cad.conceptPanelAverage != null && (
            <span className="rounded border border-workshop-border px-1.5 py-0.5">Schnitt {cad.conceptPanelAverage}</span>
          )}
          {cad.conceptPanelReverted && (
            <span className="rounded border border-workshop-warning/50 px-1.5 py-0.5 text-workshop-warning">
              vorherige Fassung
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {liveAgents.length === 0 && (
          <span
            className={`rounded-md border px-2 py-1 text-[10px] font-medium ${
              cad.status === "connecting" || cad.status === "running"
                ? "border-workshop-accent bg-workshop-accent/15 text-workshop-accent animate-pulse"
                : "border-workshop-border text-workshop-muted"
            }`}
          >
            Workflow startet…
          </span>
        )}
        {(liveAgents.length === 0 || liveAgents.includes("concept_builder")) && (
          <span
            className={`rounded-md border px-2 py-1 text-[10px] font-medium ${
              cad.currentNode === "concept_builder" || awaiting
                ? "border-workshop-accent bg-workshop-accent/15 text-workshop-accent animate-pulse"
                : cad.conceptImageUrl
                  ? "border-workshop-success/40 text-workshop-success"
                  : "border-workshop-border text-workshop-muted"
            }`}
          >
            Builder
          </span>
        )}
        {liveAgents
          .filter((id) => id !== "concept_builder")
          .map((id) => {
            const g = gradeByAgent.get(id);
            const isActive =
              reviewerId === id ||
              (cad.currentNode === "concept_panel_reviewer" && reviewerId === id) ||
              (cad.currentNode === (id as AgentNodeName) && !reviewerId);
            const waiting = queue.has(id) && !g;
            return (
              <button
                key={id}
                type="button"
                onClick={() => {
                  if (g) focusFeedback(id);
                }}
                title={g?.verdict || undefined}
                className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] font-medium ${
                  selectedFeedbackId === id
                    ? "ring-1 ring-workshop-accent"
                    : ""
                } ${
                  isActive
                    ? "border-workshop-accent bg-workshop-accent/15 text-workshop-accent animate-pulse"
                    : g
                      ? gradeClass(g.grade)
                      : waiting
                        ? "border-workshop-border text-workshop-muted"
                        : "border-workshop-border/70 text-workshop-muted"
                }`}
              >
                <span className="tabular-nums">{g?.grade ?? (waiting ? "…" : "–")}</span>
                <span>{agentLabel(id)}</span>
              </button>
            );
          })}
      </div>

      <div className="relative min-h-[140px] flex-1 overflow-hidden rounded-md border border-workshop-border bg-black/40">
        {active ? (
          <img src={active.url} alt={active.label} className="h-full w-full object-contain" />
        ) : sketchSvg ? (
          <div className="h-full min-h-[220px] overflow-auto bg-white p-2">
            <div dangerouslySetInnerHTML={{ __html: sketchSvg }} />
          </div>
        ) : (
          <div className="flex h-full min-h-[220px] flex-col items-center justify-center gap-2 px-4 text-center text-xs text-workshop-muted">
            <span className="font-semibold text-workshop-text">Konzeptphase läuft</span>
            <span>{activityLine(cad, reviewerId, awaiting)}</span>
            {cad.status === "connecting" && (
              <span className="text-[10px]">Verbindung zum Agent-Workflow…</span>
            )}
          </div>
        )}
        {active?.label && (
          <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-black/60 px-2 py-0.5 text-[10px] text-workshop-text">
            {active.label}
          </div>
        )}
      </div>

      {images.length > 1 && (
        <div className="flex gap-1.5 overflow-x-auto">
          {images.map((img, i) => (
            <button
              key={`${img.url}-${i}`}
              type="button"
              onClick={() => setActiveIdx(i)}
              className={`h-12 w-16 shrink-0 overflow-hidden rounded border ${
                i === activeIdx ? "border-workshop-accent" : "border-workshop-border opacity-70 hover:opacity-100"
              }`}
            >
              <img src={img.url} alt="" className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}

      {sortedFeedback.length > 0 && (
        <section className="flex min-h-0 max-h-[42%] flex-col rounded-md border border-workshop-border bg-workshop-panel/30">
          <header className="flex shrink-0 items-center justify-between border-b border-workshop-border/80 px-2.5 py-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-workshop-accent">
              Agenten-Feedback
            </span>
            <span className="text-[10px] text-workshop-muted">{sortedFeedback.length} Bewertung(en)</span>
          </header>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
            {sortedFeedback.map((g, i) => {
              const agentId = String(g.agent_id || `agent-${i}`);
              return (
                <FeedbackCard
                  key={`${agentId}-${i}`}
                  grade={g}
                  selected={selectedFeedbackId === agentId}
                  onSelect={() => setSelectedFeedbackId(agentId)}
                />
              );
            })}
          </div>
        </section>
      )}

      {timeline.length > 0 && (
        <section className="flex min-h-0 max-h-28 flex-col rounded-md border border-workshop-border/70 bg-black/20">
          <header className="shrink-0 border-b border-workshop-border/60 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-workshop-muted">
            Ablauf
          </header>
          <ol className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2 text-[10px] text-workshop-muted">
            {timeline.map((e, i) => (
              <li key={`${e.ts}-${e.event}-${i}`} className="leading-snug">
                <span className="font-medium text-workshop-text">{agentLabel(e.agent)}</span>
                <span className="whitespace-pre-wrap"> · {e.summary}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}
