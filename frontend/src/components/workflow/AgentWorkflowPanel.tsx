import { useEffect, useMemo, useState, type FormEvent } from "react";

import {
  useActivateStandardWorkflow,
  useActivateWorkflow,
  useAgentWorkflowConfigs,
  useDeleteWorkflowConfig,
  useSaveWorkflowConfig,
  useWorkflowOverview,
  type AgentProfile,
  type WorkflowEdge,
} from "../../hooks/useMetaCoach";

const AGENT_ORDER = [
  "supervisor",
  "flexible_specialist",
  "custom_agent_1",
  "custom_agent_2",
  "vv_manager",
  "concept_builder",
  "inventory_manager",
  "fertigung_specialist",
  "builder_3d",
  "validator",
  "montage_manager",
  "human_escalation",
] as const;

const FIXED = new Set(["supervisor"]);

function AgentCard({
  id,
  agent,
  selected,
  enabled,
  fixed,
  onSelect,
}: {
  id: string;
  agent: AgentProfile;
  selected: boolean;
  enabled: boolean;
  fixed: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-md border px-3 py-2 text-left transition ${
        selected
          ? "border-workshop-accent bg-workshop-accent/15"
          : "border-workshop-border bg-workshop-bg/40 hover:border-workshop-muted"
      } ${!enabled ? "opacity-50" : ""}`}
    >
      <div className="flex items-center justify-between gap-1">
        <div className="text-xs font-semibold text-workshop-text">{agent.display_name}</div>
        {fixed ? (
          <span className="text-[9px] font-mono uppercase text-workshop-accent">fix</span>
        ) : (
          <span className="text-[9px] font-mono uppercase text-workshop-muted">
            {enabled ? "an" : "aus"}
          </span>
        )}
      </div>
      <div className="mt-0.5 line-clamp-2 text-[10px] text-workshop-muted">{agent.role}</div>
    </button>
  );
}

function WorkflowDiagram({
  edges,
  enabledAgents,
}: {
  edges: WorkflowEdge[];
  enabledAgents: string[];
}) {
  const enabled = new Set(enabledAgents);
  const mainFlow = [
    "START",
    "supervisor",
    "flexible_specialist",
    "custom_agent_1",
    "custom_agent_2",
    "vv_manager",
    "concept_builder",
    "inventory_manager",
    "fertigung_specialist",
    "builder_3d",
    "validator",
    "montage_manager",
    "END",
  ];

  return (
    <div className="space-y-2 rounded-md border border-workshop-border bg-black/20 p-3">
      <div className="text-[10px] font-mono uppercase tracking-wider text-workshop-muted">
        Pipeline (Entwurf)
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {mainFlow.map((node, idx) => {
          const off = node !== "START" && node !== "END" && !enabled.has(node);
          return (
            <div key={node} className="flex items-center gap-1.5">
              <span
                className={`rounded border px-2 py-1 font-mono text-[10px] ${
                  node === "supervisor"
                    ? "border-workshop-accent text-workshop-accent"
                    : off
                      ? "border-workshop-border/40 text-workshop-muted line-through"
                      : "border-workshop-border text-workshop-muted"
                }`}
              >
                {node}
              </span>
              {idx < mainFlow.length - 1 && <span className="text-workshop-muted">→</span>}
            </div>
          );
        })}
      </div>
      {edges.some((e) => e.loop) && (
        <p className="text-[10px] text-workshop-muted">Loops über refinement_request · Supervisor als Router</p>
      )}
    </div>
  );
}

/** Reiter Agent Workflow: schmale DB-Spalte + Entwurf bearbeiten, dann mit Namen speichern. */
export function AgentWorkflowPanel() {
  const { data: workflow, isLoading, refetch } = useWorkflowOverview();
  const { data: configs = [], refetch: refetchConfigs } = useAgentWorkflowConfigs();
  const activate = useActivateWorkflow();
  const activateStandard = useActivateStandardWorkflow();
  const saveAs = useSaveWorkflowConfig();
  const deleteCfg = useDeleteWorkflowConfig();

  const [selectedAgentId, setSelectedAgentId] = useState("supervisor");
  const [draftAgents, setDraftAgents] = useState<Record<string, AgentProfile>>({});
  const [draftEnabled, setDraftEnabled] = useState<string[]>([]);
  const [draftMaxIter, setDraftMaxIter] = useState(6);
  const [draftTimeout, setDraftTimeout] = useState(20);
  const [draftGuidance, setDraftGuidance] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [syncKey, setSyncKey] = useState<string | null>(null);

  const activeId = workflow?.active_config?.id ?? null;
  const activeName = workflow?.active_config?.name ?? null;
  const isStandardActive = Boolean(workflow?.active_config?.is_standard);

  // Server-Stand → lokaler Entwurf (nur wenn nicht dirty / neuer Sync)
  useEffect(() => {
    if (!workflow?.agents) return;
    const key = `${activeId ?? "none"}:${workflow.profiles_updated_at ?? ""}`;
    if (dirty && syncKey === key) return;
    setDraftAgents(workflow.agents);
    setDraftEnabled(workflow.enabled_agents ?? Object.keys(workflow.agents));
    setDraftMaxIter(workflow.config?.max_iterations ?? 6);
    setDraftTimeout(workflow.config?.sandbox_timeout_seconds ?? 20);
    setDirty(false);
    setSyncKey(key);
    if (!isStandardActive && activeName) {
      setSaveName(activeName);
    } else {
      setSaveName("");
    }
  }, [workflow, activeId, activeName, isStandardActive, dirty, syncKey]);

  useEffect(() => {
    const agent = draftAgents[selectedAgentId];
    setDraftGuidance(String(agent?.properties?.guidance ?? ""));
  }, [selectedAgentId, draftAgents]);

  const orderedIds = useMemo(() => {
    const keys = Object.keys(draftAgents);
    return [
      ...AGENT_ORDER.filter((id) => id in draftAgents),
      ...keys.filter((id) => !(AGENT_ORDER as readonly string[]).includes(id)),
    ];
  }, [draftAgents]);

  const selectedAgent = draftAgents[selectedAgentId];
  const busy = activate.isPending || activateStandard.isPending || saveAs.isPending || deleteCfg.isPending;

  const markDirty = () => setDirty(true);

  const flash = (ok: string | null, err: string | null) => {
    setStatusMsg(ok);
    setErrorMsg(err);
  };

  const loadConfig = (id: string, isStandard: boolean) => {
    const done = () => {
      setDirty(false);
      setSyncKey(null);
      void refetch();
      void refetchConfigs();
      flash(null, null);
    };
    if (isStandard) {
      activateStandard.mutate(undefined, {
        onSuccess: () => {
          done();
          flash("Standard geladen.", null);
        },
        onError: (e) => flash(null, e instanceof Error ? e.message : "Laden fehlgeschlagen"),
      });
    } else {
      activate.mutate(id, {
        onSuccess: (row) => {
          done();
          setSaveName(row.name);
          flash(`„${row.name}“ geladen.`, null);
        },
        onError: (e) => flash(null, e instanceof Error ? e.message : "Laden fehlgeschlagen"),
      });
    }
  };

  const toggleAgent = (id: string) => {
    if (FIXED.has(id)) return;
    setDraftEnabled((prev) => (prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]));
    markDirty();
  };

  const applyGuidanceToDraft = () => {
    setDraftAgents((prev) => {
      const agent = prev[selectedAgentId];
      if (!agent) return prev;
      return {
        ...prev,
        [selectedAgentId]: {
          ...agent,
          properties: { ...(agent.properties ?? {}), guidance: draftGuidance },
        },
      };
    });
    markDirty();
    flash("Guidance im Entwurf übernommen – noch speichern.", null);
  };

  const handleSave = (event: FormEvent) => {
    event.preventDefault();
    const name = saveName.trim();
    if (!name) {
      flash(null, "Bitte einen Namen für die Konfiguration vergeben.");
      return;
    }
    if (name.toLowerCase() === "standard") {
      flash(null, "Name „Standard“ ist reserviert.");
      return;
    }
    // Guidance des aktuellen Agenten mitnehmen
    const agents = { ...draftAgents };
    if (agents[selectedAgentId]) {
      agents[selectedAgentId] = {
        ...agents[selectedAgentId],
        properties: { ...(agents[selectedAgentId].properties ?? {}), guidance: draftGuidance },
      };
    }
    saveAs.mutate(
      {
        name,
        agents,
        enabled_agents: draftEnabled,
        config: {
          max_iterations: draftMaxIter,
          sandbox_timeout_seconds: draftTimeout,
        },
        activate: true,
      },
      {
        onSuccess: (row) => {
          setDirty(false);
          setSyncKey(null);
          setSaveName(row.name);
          void refetch();
          void refetchConfigs();
          flash(`„${row.name}“ gespeichert und aktiv.`, null);
        },
        onError: (e) => flash(null, e instanceof Error ? e.message : "Speichern fehlgeschlagen"),
      },
    );
  };

  return (
    <div className="flex h-full min-h-0 gap-3">
      {/* Schmale Spalte: gespeicherte Konfigurationen */}
      <aside className="flex w-40 shrink-0 flex-col gap-2 border-r border-workshop-border pr-2 sm:w-44">
        <div className="text-[10px] font-mono uppercase tracking-wider text-workshop-muted">
          Gespeichert
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {configs.map((c) => {
            const active = c.is_active;
            return (
              <div
                key={c.id}
                className={`group rounded-md border px-2 py-1.5 ${
                  active
                    ? "border-workshop-accent bg-workshop-accent/15"
                    : "border-workshop-border hover:border-workshop-muted"
                }`}
              >
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => loadConfig(c.id, c.is_standard)}
                  className="w-full text-left disabled:opacity-50"
                  title={c.description || c.name}
                >
                  <div className="truncate text-xs font-semibold text-workshop-text">{c.name}</div>
                  <div className="text-[9px] text-workshop-muted">
                    {c.is_standard ? "Standard" : "User"}
                    {active ? " · aktiv" : ""}
                  </div>
                </button>
                {!c.is_standard && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      deleteCfg.mutate(c.id, {
                        onSuccess: () => {
                          void refetch();
                          void refetchConfigs();
                          flash(`„${c.name}“ gelöscht.`, null);
                        },
                        onError: (e) =>
                          flash(null, e instanceof Error ? e.message : "Löschen fehlgeschlagen"),
                      })
                    }
                    className="mt-1 text-[9px] text-workshop-danger opacity-70 hover:opacity-100 disabled:opacity-40"
                  >
                    Löschen
                  </button>
                )}
              </div>
            );
          })}
          {configs.length === 0 && !isLoading && (
            <p className="text-[10px] text-workshop-muted">Noch keine Einträge.</p>
          )}
        </div>
      </aside>

      {/* Hauptbereich: Entwurf */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto">
        {isLoading && <p className="text-xs text-workshop-muted">Lade Workflow…</p>}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs text-workshop-muted">
            Entwurf auf Basis von{" "}
            <span className="font-semibold text-workshop-text">{activeName ?? "—"}</span>
            {dirty ? " · ungespeicherte Änderungen" : ""}
            {isStandardActive ? " · Standard unveränderlich in der DB" : ""}
          </div>
        </div>

        {Object.keys(draftAgents).length > 0 && (
          <>
            <WorkflowDiagram edges={workflow?.edges ?? []} enabledAgents={draftEnabled} />

            <div className="rounded-md border border-workshop-border bg-workshop-bg/40 p-3 text-xs">
              <div className="mb-2 text-[10px] font-mono uppercase text-workshop-muted">Laufzeit</div>
              <div className="flex flex-wrap gap-3">
                <label className="flex flex-col gap-1">
                  <span className="text-workshop-muted">max_iterations</span>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={draftMaxIter}
                    onChange={(e) => {
                      setDraftMaxIter(Number(e.target.value));
                      markDirty();
                    }}
                    className="w-24 rounded border border-workshop-border bg-workshop-bg px-2 py-1"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-workshop-muted">sandbox_timeout (s)</span>
                  <input
                    type="number"
                    min={5}
                    max={300}
                    value={draftTimeout}
                    onChange={(e) => {
                      setDraftTimeout(Number(e.target.value));
                      markDirty();
                    }}
                    className="w-24 rounded border border-workshop-border bg-workshop-bg px-2 py-1"
                  />
                </label>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              {orderedIds.map((id) => (
                <div key={id} className="space-y-1">
                  <AgentCard
                    id={id}
                    agent={draftAgents[id]}
                    selected={id === selectedAgentId}
                    enabled={draftEnabled.includes(id)}
                    fixed={FIXED.has(id)}
                    onSelect={() => setSelectedAgentId(id)}
                  />
                  {!FIXED.has(id) && (
                    <button
                      type="button"
                      onClick={() => toggleAgent(id)}
                      className="w-full rounded border border-workshop-border px-2 py-1 text-[10px] text-workshop-muted hover:text-workshop-text"
                    >
                      {draftEnabled.includes(id) ? "Deaktivieren" : "Aktivieren"}
                    </button>
                  )}
                </div>
              ))}
            </div>

            {selectedAgent && (
              <div className="space-y-2 rounded-md border border-workshop-border bg-workshop-bg/50 p-3 text-xs">
                <div className="font-semibold text-workshop-text">{selectedAgent.display_name}</div>
                <p className="text-workshop-muted">{selectedAgent.role}</p>
                <div className="font-mono text-[10px] text-workshop-muted">{selectedAgentId}</div>
                <div>
                  <div className="mb-1 text-[10px] font-mono uppercase text-workshop-muted">Guidance</div>
                  <textarea
                    value={draftGuidance}
                    onChange={(e) => {
                      setDraftGuidance(e.target.value);
                      markDirty();
                    }}
                    rows={4}
                    className="w-full resize-y rounded border border-workshop-border bg-black/30 p-2 text-[11px] text-workshop-text"
                  />
                  <button
                    type="button"
                    onClick={applyGuidanceToDraft}
                    className="mt-2 rounded-md border border-workshop-border px-3 py-1.5 text-[11px] text-workshop-text"
                  >
                    Guidance in Entwurf übernehmen
                  </button>
                </div>
              </div>
            )}

            <form
              onSubmit={handleSave}
              className="sticky bottom-0 flex flex-wrap items-end gap-2 rounded-md border border-workshop-border bg-workshop-panel p-3"
            >
              <label className="min-w-[12rem] flex-1">
                <span className="mb-1 block text-[10px] font-mono uppercase text-workshop-muted">
                  Speichern unter Name
                </span>
                <input
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  placeholder={isStandardActive ? "z. B. Mein Workflow" : "Name der Konfiguration"}
                  className="w-full rounded-md border border-workshop-border bg-workshop-bg px-2 py-1.5 text-xs"
                />
              </label>
              <button
                type="submit"
                disabled={busy || !saveName.trim()}
                className="rounded-md bg-workshop-accent px-4 py-2 text-xs font-semibold text-workshop-bg disabled:opacity-40"
              >
                Speichern
              </button>
              <p className="basis-full text-[10px] text-workshop-muted">
                Änderungen zuerst im Entwurf, dann speichern. Nur der Supervisor ist immer fix; Flexible,
                Inventory und die Leer-Agenten sind optional. Leer-Agenten 1/2 sind in Standard aus und
                wirken nur über Guidance. „Standard“ kann nicht gelöscht werden.
              </p>
            </form>
          </>
        )}

        {statusMsg && <p className="text-xs text-workshop-success">{statusMsg}</p>}
        {errorMsg && <p className="text-xs text-workshop-danger">{errorMsg}</p>}
      </div>
    </div>
  );
}
