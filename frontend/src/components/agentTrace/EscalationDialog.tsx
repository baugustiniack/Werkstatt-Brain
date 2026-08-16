/** Human-in-the-Loop-Dialoge für Freigabe und Klärungsfragen. */

import { useState } from "react";

import type { ConceptDecision, EscalationPayload } from "../../api/types";
import { apiBaseUrl } from "../../api/client";

interface EscalationDialogProps {
  escalation: EscalationPayload;
  onDecision: (decision: ConceptDecision | Record<string, unknown>) => void;
}

function resolveMediaUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.startsWith("http") ? url : `${apiBaseUrl()}${url}`;
}

function normalizeReason(reason: string | undefined | null): string {
  return String(reason || "")
    .trim()
    .toLowerCase();
}

function ConceptApprovalDialog({ escalation, onDecision }: EscalationDialogProps) {
  const [mode, setMode] = useState<"view" | "revise">("view");
  const [feedback, setFeedback] = useState("");
  const parts = escalation.requirements_contract?.parts ?? [];
  const title = escalation.requirements_contract?.project_title ?? "Konzept-Entwurf";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg border border-workshop-accent bg-workshop-panel p-5 shadow-xl">
        <h3 className="mb-2 text-sm font-semibold text-workshop-accent">Konzept freigeben: {title}</h3>
        <p className="mb-3 text-xs text-workshop-muted">{parts.length} Teil(e) – bitte prüfen.</p>
        {escalation.concept_sketch_svg && (
          <div
            className="mb-3 max-h-48 overflow-auto rounded border border-workshop-border bg-white p-2"
            dangerouslySetInnerHTML={{ __html: escalation.concept_sketch_svg }}
          />
        )}
        {mode === "view" ? (
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setMode("revise")}
              className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text"
            >
              Anpassen
            </button>
            <button
              type="button"
              onClick={() => onDecision({ decision: "approve" })}
              className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg"
            >
              Freigeben
            </button>
          </div>
        ) : (
          <>
            <textarea
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="Was soll am Konzept geändert werden?"
              rows={3}
              className="mb-3 w-full resize-none rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs text-workshop-text"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setMode("view")}
                className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-muted"
              >
                Abbrechen
              </button>
              <button
                type="button"
                disabled={!feedback.trim()}
                onClick={() => onDecision({ decision: "revise", feedback: feedback.trim() })}
                className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
              >
                Überarbeitung senden
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** Klärungsfrage (V&V oder Konzept) – inline oder als Modal. */
export function ClarificationPanel({
  escalation,
  onDecision,
  variant = "modal",
}: EscalationDialogProps & { variant?: "modal" | "inline" }) {
  const [answer, setAnswer] = useState("");
  const reason = normalizeReason(escalation.reason);
  const isConceptClarify =
    reason === "concept_clarification" ||
    String(escalation.phase || "").toLowerCase().includes("concept") ||
    String(escalation.phase || "").toLowerCase().includes("klär");

  const critique = escalation.coherence_critique;
  const openFromCritique = [
    ...(critique?.must_ask_user ?? []),
    ...(critique?.contradictions ?? []).map((x) => `Widerspruch: ${x}`),
    ...(critique?.missing_information ?? []).map((x) => `Fehlt: ${x}`),
    ...(critique?.logic_gaps ?? []).map((x) => `Lücke: ${x}`),
    ...(critique?.concept_issues ?? []).map((x) => `Konzept: ${x}`),
  ];

  const question =
    (escalation.question || "").trim() ||
    openFromCritique[0] ||
    (isConceptClarify
      ? "Bitte die offenen Punkte zum Konzept spezifizieren."
      : "Bitte die offene Anforderungsfrage beantworten.");

  const idx = (escalation.question_index ?? 0) + 1;
  const total = Math.max(escalation.question_total ?? 1, idx);
  const answered = escalation.answered_so_far ?? [];
  const title =
    escalation.draft_title ||
    escalation.requirements_contract?.project_title ||
    (isConceptClarify ? "Konzept" : "Anforderungen");

  const previewUrl =
    resolveMediaUrl(escalation.concept_image_urls?.[0]?.url) ||
    resolveMediaUrl(escalation.concept_image_url);

  const parts = escalation.requirements_contract?.parts ?? [];

  const body = (
    <div
      className={
        variant === "inline"
          ? "flex w-full flex-col overflow-hidden rounded-lg border border-workshop-accent bg-workshop-panel shadow-md"
          : "flex max-h-[90vh] w-full max-w-xl flex-col overflow-hidden rounded-lg border border-workshop-accent bg-workshop-panel shadow-xl"
      }
    >
      <div className="border-b border-workshop-border px-4 py-3 sm:px-5">
        <h3 className="text-sm font-semibold text-workshop-accent">
          {isConceptClarify
            ? `Konzept-Klärung – Frage ${idx} von ${total}`
            : `V&V Klärung – Frage ${idx} von ${total}`}
        </h3>
        <p className="mt-0.5 text-[11px] text-workshop-muted">
          {isConceptClarify
            ? `Offene Punkte zu „${title}“ müssen geklärt werden, bevor das Konzept freigegeben wird.`
            : "Bitte beantworte die Fragen nacheinander."}
        </p>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3 sm:px-5">
        {(parts.length > 0 || previewUrl) && (
          <div className="rounded border border-workshop-border/70 bg-black/20 p-2">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-workshop-muted">
              Bezug: {title}
              {parts.length > 0 ? ` · ${parts.length} Teil(e)` : ""}
            </div>
            {previewUrl && (
              <img
                src={previewUrl}
                alt="Konzept"
                className="mb-1 max-h-28 w-full rounded object-cover"
              />
            )}
            {parts.length > 0 && (
              <p className="text-[11px] text-workshop-text">
                {parts
                  .slice(0, 8)
                  .map((p) => p.name || "?")
                  .join(", ")}
                {parts.length > 8 ? "…" : ""}
              </p>
            )}
          </div>
        )}

        {critique &&
          (critique.summary ||
            (critique.contradictions?.length ?? 0) > 0 ||
            (critique.missing_information?.length ?? 0) > 0) && (
            <div className="rounded border border-workshop-warning/50 bg-workshop-warning/10 px-2.5 py-2">
              <div className="mb-1 text-[10px] font-semibold uppercase text-workshop-warning">
                Prüfung Bild ↔ Text ↔ Konzept
                {critique.severity ? ` · ${critique.severity}` : ""}
              </div>
              {critique.summary && (
                <p className="mb-1 text-[11px] leading-snug text-workshop-text">{critique.summary}</p>
              )}
              {(
                [
                  ["Widersprüche", critique.contradictions],
                  ["Fehlende Infos", critique.missing_information],
                  ["Logiklücken", critique.logic_gaps],
                  ["Konzept-Mängel", critique.concept_issues],
                ] as const
              ).map(([label, items]) =>
                items && items.length > 0 ? (
                  <div key={label} className="mb-1">
                    <div className="text-[10px] font-semibold text-workshop-muted">{label}</div>
                    <ul className="list-inside list-disc text-[11px] text-workshop-text">
                      {items.slice(0, 4).map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null,
              )}
            </div>
          )}

        {answered.length > 0 && (
          <div className="max-h-24 overflow-y-auto rounded-md border border-workshop-border/60 bg-black/20 p-2 text-[11px] text-workshop-muted">
            {answered.map((qa, i) => (
              <div key={i} className="mb-1 last:mb-0">
                <span className="font-semibold text-workshop-text">Q{i + 1}:</span> {qa.answer}
              </div>
            ))}
          </div>
        )}

        <div className="rounded-md border border-workshop-accent/40 bg-workshop-accent/10 px-3 py-2">
          <div className="mb-1 text-[10px] font-semibold uppercase text-workshop-accent">Aktuelle Frage</div>
          <p className="text-sm leading-snug text-workshop-text">{question}</p>
        </div>

        <textarea
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          placeholder="Deine Antwort…"
          rows={3}
          autoFocus
          className="w-full resize-none rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none"
        />
      </div>

      <div className="flex justify-end gap-2 border-t border-workshop-border px-4 py-3 sm:px-5">
        <button
          type="button"
          onClick={() => onDecision({ decision: "answer", answer: "(übersprungen)" })}
          className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-muted hover:bg-workshop-bg"
        >
          Überspringen
        </button>
        <button
          type="button"
          disabled={!answer.trim()}
          onClick={() => onDecision({ decision: "answer", answer: answer.trim() })}
          className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
        >
          {idx >= total
            ? isConceptClarify
              ? "Antworten übernehmen"
              : "Weiter zur Anforderungsliste"
            : "Nächste Frage"}
        </button>
      </div>
    </div>
  );

  if (variant === "inline") {
    return body;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">{body}</div>
  );
}

function ClarificationDialog({ escalation, onDecision }: EscalationDialogProps) {
  return <ClarificationPanel escalation={escalation} onDecision={onDecision} variant="modal" />;
}

function RequirementsConfirmDialog({ escalation, onDecision }: EscalationDialogProps) {
  const [mode, setMode] = useState<"view" | "revise">("view");
  const [feedback, setFeedback] = useState("");
  const vv = escalation.vv_requirements;
  const title = vv?.title || "Anforderungsliste";
  const phase = escalation.phase || vv?.phase || "concept";
  const summary = escalation.summary || vv?.summary;
  const requirements = vv?.requirements ?? [];
  const qa = escalation.qa_answers ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg border border-workshop-accent bg-workshop-panel p-5 shadow-xl">
        <h3 className="mb-1 text-sm font-semibold text-workshop-accent">
          Anforderungsliste bestätigen ({phase}): {title}
        </h3>
        <p className="mb-3 text-xs text-workshop-muted">
          Aus deinen Antworten wurde die vollständige Anforderungsliste erstellt. Bitte bestätigen oder anpassen.
        </p>

        {summary && <p className="mb-3 text-xs text-workshop-text">{summary}</p>}

        {qa.length > 0 && (
          <details className="mb-3 rounded-md border border-workshop-border/60 p-2 text-[11px] text-workshop-muted">
            <summary className="cursor-pointer font-semibold text-workshop-text">Bisherige Antworten</summary>
            <ul className="mt-1 list-inside list-disc">
              {qa.map((item, i) => (
                <li key={i}>
                  {item.question}: {item.answer}
                </li>
              ))}
            </ul>
          </details>
        )}

        <div className="mb-3 max-h-48 overflow-y-auto rounded border border-workshop-border bg-black/20 p-2 text-xs">
          {requirements.map((req, i) => (
            <div key={req.id || i} className="mb-1.5 border-b border-workshop-border/40 pb-1 last:border-0">
              <span className="font-semibold text-workshop-accent">{req.id || `R${i + 1}`}</span>{" "}
              <span className="text-workshop-text">{req.text}</span>
              {req.priority ? (
                <span className="ml-1 text-[10px] text-workshop-muted">({req.priority})</span>
              ) : null}
            </div>
          ))}
        </div>

        {mode === "revise" && (
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="Was soll an der Liste geändert werden?"
            rows={3}
            className="mb-3 w-full resize-none rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs"
          />
        )}

        <div className="flex justify-end gap-2">
          {mode === "view" ? (
            <>
              <button
                type="button"
                onClick={() => setMode("revise")}
                className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold"
              >
                Anpassen
              </button>
              <button
                type="button"
                onClick={() => onDecision({ decision: "approve" })}
                className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg"
              >
                Anforderungsliste freigeben
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setMode("view")}
                className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-muted"
              >
                Abbrechen
              </button>
              <button
                type="button"
                disabled={!feedback.trim()}
                onClick={() => onDecision({ decision: "revise", feedback: feedback.trim() })}
                className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
              >
                Anpassung senden
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** Human-in-the-Loop-Dialog. */
export function EscalationDialog({ escalation, onDecision }: EscalationDialogProps) {
  const reason = normalizeReason(escalation.reason);

  if (reason === "concept_approval") {
    return <ConceptApprovalDialog escalation={escalation} onDecision={onDecision} />;
  }

  if (
    reason === "requirements_question" ||
    reason === "concept_clarification" ||
    reason.includes("clarif") ||
    reason.includes("klär")
  ) {
    return <ClarificationDialog escalation={escalation} onDecision={onDecision} />;
  }

  if (reason === "requirements_confirm" || reason === "requirements_approval") {
    return <RequirementsConfirmDialog escalation={escalation} onDecision={onDecision} />;
  }

  // Unbekannte Gates: trotzdem Inhalt zeigen, nicht nur den Reason-String
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-lg rounded-lg border border-workshop-warning bg-workshop-panel p-5 shadow-xl">
        <h3 className="mb-2 text-sm font-semibold text-workshop-warning">Eskalation – Entscheidung nötig</h3>
        <p className="mb-2 text-xs text-workshop-muted">Typ: {escalation.reason || "(unbekannt)"}</p>
        {escalation.question && (
          <p className="mb-3 text-sm text-workshop-text">{escalation.question}</p>
        )}
        {escalation.coherence_critique?.summary && (
          <p className="mb-3 text-xs text-workshop-text">{escalation.coherence_critique.summary}</p>
        )}
        {escalation.iteration_count > 0 && (
          <p className="mb-3 text-xs text-workshop-muted">Nach {escalation.iteration_count} Korrekturschleifen.</p>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => onDecision({ decision: "revise", feedback: "Abgelehnt / nacharbeiten" })}
            className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text"
          >
            Ablehnen
          </button>
          <button
            type="button"
            onClick={() => onDecision({ decision: "approve" })}
            className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg"
          >
            Fortsetzen
          </button>
        </div>
      </div>
    </div>
  );
}
