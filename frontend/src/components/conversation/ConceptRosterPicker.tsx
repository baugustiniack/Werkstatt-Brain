export const CONCEPT_ROSTER_SELECTABLE = [
  "flexible_specialist",
  "interior_architect",
  "vv_manager",
  "concept_critic",
  "fertigung_specialist",
] as const;

export const CONCEPT_ROSTER_LABELS: Record<string, string> = {
  flexible_specialist: "Flexible Specialist",
  interior_architect: "Innenarchitekt",
  vv_manager: "V&V Manager",
  concept_critic: "Concept Critic",
  fertigung_specialist: "Fertigung",
};

export type ConceptRosterMode = "auto" | "manual";

export function ConceptRosterPicker({
  mode,
  agents,
  selectable = CONCEPT_ROSTER_SELECTABLE,
  onModeChange,
  onAgentsChange,
  compact = false,
  disabled = false,
}: {
  mode: ConceptRosterMode;
  agents: string[];
  selectable?: readonly string[];
  onModeChange: (mode: ConceptRosterMode) => void;
  onAgentsChange: (agents: string[]) => void;
  compact?: boolean;
  disabled?: boolean;
}) {
  const toggleAgent = (id: string) => {
    if (disabled) return;
    const next = agents.includes(id) ? agents.filter((a) => a !== id) : [...agents, id];
    onAgentsChange(next);
  };

  return (
    <div className={compact ? "space-y-1.5" : "space-y-2"}>
      <div className="flex flex-wrap gap-1">
        {(
          [
            ["auto", "Supervisor entscheidet"],
            ["manual", "Eigene Auswahl"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            disabled={disabled}
            onClick={() => onModeChange(value)}
            className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
              mode === value
                ? "bg-workshop-accent text-workshop-bg"
                : "border border-workshop-border text-workshop-muted hover:bg-workshop-bg"
            } disabled:opacity-50`}
          >
            {label}
          </button>
        ))}
      </div>
      {mode === "manual" && (
        <div className="flex flex-wrap gap-1.5">
          {selectable.map((id) => {
            const active = agents.includes(id);
            return (
              <button
                key={id}
                type="button"
                disabled={disabled}
                onClick={() => toggleAgent(id)}
                className={`rounded border px-2 py-0.5 text-[11px] font-medium ${
                  active
                    ? "border-workshop-accent bg-workshop-accent/15 text-workshop-text"
                    : "border-workshop-border text-workshop-muted hover:border-workshop-muted"
                } disabled:opacity-50`}
              >
                {CONCEPT_ROSTER_LABELS[id] ?? id}
              </button>
            );
          })}
        </div>
      )}
      {mode === "manual" && agents.length === 0 && (
        <p className="text-[10px] text-workshop-danger">Mindestens einen Agenten wählen.</p>
      )}
      {!compact && (
        <p className="text-[10px] text-workshop-muted">
          Concept Builder läuft immer. Bei „Supervisor“ wählt die Komplexitäts-Heuristik die Jury (z. B. ohne
          Fertigung bei Lageplänen).
        </p>
      )}
    </div>
  );
}
