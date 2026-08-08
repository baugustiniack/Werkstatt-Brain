import { useState } from "react";

import type { EscalationPayload } from "../../api/types";

interface EscalationDialogProps {
  escalation: EscalationPayload;
  onDecision: (decision: unknown) => void;
}

/** 2D-Konzept-Freigabe-Gate (Nutzer-Feedback: erst Entwurf bestätigen, dann
 * 3D-Ausarbeitung starten lassen – SPEC Kap. 3.3/3.5, 5.2 Panel 2). */
function ConceptApprovalDialog({ escalation, onDecision }: EscalationDialogProps) {
  const [mode, setMode] = useState<"view" | "revise">("view");
  const [feedback, setFeedback] = useState("");
  const parts = escalation.requirements_contract?.parts ?? [];
  const title = escalation.requirements_contract?.project_title ?? "Konzept-Entwurf";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg border border-workshop-accent bg-workshop-panel p-5 shadow-xl">
        <h3 className="mb-1 text-sm font-semibold text-workshop-accent">📐 Entwurf zur Freigabe: {title}</h3>
        <p className="mb-3 text-xs text-workshop-muted">
          Bevor die 3D-Ausarbeitung startet, prüfe den 2D-Entwurf. Bei mehreren Teilen werden diese nacheinander
          ausgearbeitet.
        </p>

        {escalation.concept_sketch_svg && (
          <div
            className="mb-3 flex-1 overflow-auto rounded-md border border-workshop-border bg-black/30 p-2"
            dangerouslySetInnerHTML={{ __html: escalation.concept_sketch_svg }}
          />
        )}

        <div className="mb-3 flex flex-col gap-1 overflow-y-auto text-xs">
          {parts.map((part, idx) => (
            <div key={idx} className="rounded-md border border-workshop-border p-2">
              <div className="font-semibold text-workshop-text">
                {idx + 1}. {part.name}
              </div>
              <div className="text-workshop-muted">
                {part.functional_geometry?.dimensions_mm?.x}×{part.functional_geometry?.dimensions_mm?.y}×
                {part.functional_geometry?.dimensions_mm?.z}mm · {part.material_tool_constraints?.material_type}
              </div>
              {part.manufacturing_features?.length > 0 && (
                <div className="text-workshop-muted">Features: {part.manufacturing_features.join(", ")}</div>
              )}
            </div>
          ))}
        </div>

        {mode === "revise" ? (
          <div className="flex flex-col gap-2">
            <textarea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              placeholder="Was soll am Entwurf geändert werden? (z.B. 'Schreibtisch weglassen', 'Zimmer ist 3x4m groß')"
              rows={3}
              autoFocus
              className="w-full resize-none rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setMode("view")}
                className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-bg"
              >
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
              className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-bg"
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
      </div>
    </div>
  );
}

/** Human-in-the-Loop-Dialog bei unlösbaren Geometriekonflikten/fehlendem Material (SPEC Kap. 3.5, 5.2 Panel 2). */
export function EscalationDialog({ escalation, onDecision }: EscalationDialogProps) {
  const [note, setNote] = useState("");

  if (escalation.reason === "concept_approval") {
    return <ConceptApprovalDialog escalation={escalation} onDecision={onDecision} />;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-lg rounded-lg border border-workshop-warning bg-workshop-panel p-5 shadow-xl">
        <h3 className="mb-2 text-sm font-semibold text-workshop-warning">⚠ Eskalation – Freigabe erforderlich</h3>
        <p className="mb-3 text-sm text-workshop-text">{escalation.reason}</p>

        {escalation.iteration_count > 0 && (
          <p className="mb-3 text-xs text-workshop-muted">Nach {escalation.iteration_count} Korrekturschleifen.</p>
        )}

        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Optionale Anmerkung zur Entscheidung…"
          rows={2}
          className="mb-3 w-full resize-none rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none"
        />

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => onDecision({ approved: false, note })}
            className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-bg"
          >
            Ablehnen
          </button>
          <button
            type="button"
            onClick={() => onDecision({ approved: true, note })}
            className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg"
          >
            Freigeben &amp; Fortsetzen
          </button>
        </div>
      </div>
    </div>
  );
}
