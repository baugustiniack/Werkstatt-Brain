import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  useMetaCoachChat,
  useMetaCoachLogs,
  useWorkflowOverview,
  type AgentProfile,
  type MetaCoachChatMessage,
  type WorkflowEdge,
} from "../../hooks/useMetaCoach";

const AGENT_ORDER = [
  "supervisor",
  "flexible_specialist",
  "vv_manager",
  "concept_builder",
  "inventory_manager",
  "fertigung_specialist",
  "builder_3d",
  "validator",
  "montage_manager",
  "human_escalation",
] as const;

function AgentCard({
  id,
  agent,
  selected,
  onSelect,
}: {
  id: string;
  agent: AgentProfile;
  selected: boolean;
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
      }`}
    >
      <div className="text-xs font-semibold text-workshop-text">{agent.display_name}</div>
      <div className="mt-0.5 line-clamp-2 text-[10px] text-workshop-muted">{agent.role}</div>
    </button>
  );
}

function WorkflowDiagram({ edges }: { edges: WorkflowEdge[] }) {
  const mainFlow = [
    "START",
    "supervisor",
    "flexible_specialist",
    "vv_manager",
    "concept_builder",
    "inventory_manager",
    "fertigung_specialist",
    "builder_3d",
    "validator",
    "montage_manager",
    "END",
  ];
  const loopEdges = edges.filter((e) => e.loop);

  return (
    <div className="space-y-3 rounded-md border border-workshop-border bg-black/20 p-3">
      <div className="text-[10px] font-mono uppercase tracking-wider text-workshop-muted">
        Hub-and-Spoke · Supervisor als Router
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {mainFlow.map((node, idx) => (
          <div key={node} className="flex items-center gap-1.5">
            <span
              className={`rounded border px-2 py-1 font-mono text-[10px] ${
                node === "supervisor"
                  ? "border-workshop-accent text-workshop-accent"
                  : "border-workshop-border text-workshop-muted"
              }`}
            >
              {node}
            </span>
            {idx < mainFlow.length - 1 && <span className="text-workshop-muted">→</span>}
          </div>
        ))}
      </div>
      <p className="text-[11px] text-workshop-muted">
        Jeder Fach-Agent kehrt nach seiner Arbeit zum <strong className="text-workshop-text">Supervisor</strong>{" "}
        zurück. Korrekturschleifen laufen über <code className="text-workshop-accent">refinement_request</code>.
      </p>
      {loopEdges.length > 0 && (
        <div className="space-y-1 border-t border-workshop-border/60 pt-2">
          <div className="text-[10px] font-mono uppercase text-workshop-muted">Loops</div>
          {loopEdges.map((e, i) => (
            <div key={`${e.from}-${e.to}-${i}`} className="text-[11px] text-workshop-text">
              <span className="font-mono text-workshop-accent">
                {e.from} ↻ {e.to}
              </span>
              {e.when ? <span className="text-workshop-muted"> — {e.when}</span> : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentDetail({ id, agent }: { id: string; agent: AgentProfile }) {
  const guidance = String(agent.properties?.guidance ?? "");
  const notes = String(agent.properties?.notes ?? "");

  return (
    <div className="space-y-3 rounded-md border border-workshop-border bg-workshop-bg/50 p-3 text-xs">
      <div>
        <div className="font-semibold text-workshop-text">{agent.display_name}</div>
        <div className="mt-1 text-workshop-muted">{agent.role}</div>
        <div className="mt-1 font-mono text-[10px] text-workshop-muted">{id}</div>
      </div>
      {agent.responsibilities && agent.responsibilities.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-mono uppercase text-workshop-muted">Aufgaben</div>
          <ul className="list-inside list-disc space-y-0.5 text-workshop-text">
            {agent.responsibilities.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <div className="mb-1 text-[10px] font-mono uppercase text-workshop-muted">Inputs</div>
          <p className="text-workshop-text">{(agent.inputs ?? []).join(", ") || "—"}</p>
        </div>
        <div>
          <div className="mb-1 text-[10px] font-mono uppercase text-workshop-muted">Outputs</div>
          <p className="text-workshop-text">{(agent.outputs ?? []).join(", ") || "—"}</p>
        </div>
      </div>
      <div>
        <div className="mb-1 text-[10px] font-mono uppercase text-workshop-muted">Meta-Coach Guidance</div>
        <pre className="max-h-28 overflow-auto whitespace-pre-wrap rounded border border-workshop-border bg-black/30 p-2 text-[11px] text-workshop-text">
          {guidance || "(noch keine – Meta-Coach kann sie setzen)"}
        </pre>
      </div>
      {notes && (
        <div>
          <div className="mb-1 text-[10px] font-mono uppercase text-workshop-muted">Notizen</div>
          <p className="text-workshop-muted">{notes}</p>
        </div>
      )}
    </div>
  );
}

/** Reiter Agent Workflow: Topologie + Eigenschaften + Meta-Coach-Chat. */
export function AgentWorkflowPanel() {
  const { data: workflow, isLoading, refetch } = useWorkflowOverview();
  const { data: logs = [] } = useMetaCoachLogs();
  const chatMutation = useMetaCoachChat();

  const [selectedAgentId, setSelectedAgentId] = useState<string>("supervisor");
  const [selectedLogs, setSelectedLogs] = useState<string[]>([]);
  const [messages, setMessages] = useState<MetaCoachChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const chatScrollRef = useRef<HTMLDivElement>(null);

  const agents = workflow?.agents ?? {};
  const orderedIds = useMemo(() => {
    const keys = Object.keys(agents);
    return [
      ...AGENT_ORDER.filter((id) => id in agents),
      ...keys.filter((id) => !(AGENT_ORDER as readonly string[]).includes(id)),
    ];
  }, [agents]);

  useEffect(() => {
    if (!selectedAgentId && orderedIds.length) setSelectedAgentId(orderedIds[0]);
  }, [orderedIds, selectedAgentId]);

  useEffect(() => {
    chatScrollRef.current?.scrollTo({ top: chatScrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, chatMutation.isPending]);

  const selectedAgent = agents[selectedAgentId];

  const toggleLog = (name: string) => {
    setSelectedLogs((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]));
  };

  const handleSend = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || chatMutation.isPending) return;
    const nextMessages: MetaCoachChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(nextMessages);
    setDraft("");
    try {
      const result = await chatMutation.mutateAsync({
        messages: nextMessages,
        log_names: selectedLogs.length ? selectedLogs : undefined,
        apply_actions: true,
      });
      setMessages((prev) => [...prev, { role: "assistant", content: result.reply }]);
      if (result.applied_changes?.length) {
        void refetch();
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Fehler: ${err instanceof Error ? err.message : "Meta-Coach nicht erreichbar."}`,
        },
      ]);
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-3 xl:grid-cols-2">
      {/* Links: Topologie + Agenten */}
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
        {isLoading && <p className="text-xs text-workshop-muted">Lade Workflow…</p>}
        {workflow && (
          <>
            <WorkflowDiagram edges={workflow.edges} />
            <div className="rounded-md border border-workshop-border bg-workshop-bg/40 p-3 text-xs">
              <div className="mb-2 text-[10px] font-mono uppercase text-workshop-muted">Laufzeit-Config</div>
              <div className="flex flex-wrap gap-3 text-workshop-text">
                <span>
                  max_iterations:{" "}
                  <strong className="text-workshop-accent">{workflow.config.max_iterations}</strong>
                </span>
                <span>
                  sandbox_timeout:{" "}
                  <strong className="text-workshop-accent">{workflow.config.sandbox_timeout_seconds}s</strong>
                </span>
              </div>
              {workflow.config.description && (
                <p className="mt-2 text-workshop-muted">{workflow.config.description}</p>
              )}
              {workflow.config.notes && (
                <p className="mt-1 text-[11px] text-workshop-muted">Notizen: {workflow.config.notes}</p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {orderedIds.map((id) => (
                <AgentCard
                  key={id}
                  id={id}
                  agent={agents[id]}
                  selected={id === selectedAgentId}
                  onSelect={() => setSelectedAgentId(id)}
                />
              ))}
            </div>
            {selectedAgent && <AgentDetail id={selectedAgentId} agent={selectedAgent} />}
          </>
        )}
      </div>

      {/* Rechts: Meta-Coach Chat */}
      <div className="flex min-h-0 flex-col gap-2 rounded-md border border-workshop-border bg-workshop-panel/50 p-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-workshop-text">Meta-Coach</div>
            <p className="text-[11px] text-workshop-muted">
              Analysiert Logging-Files und passt Agent-Eigenschaften / Workflow mit dir gemeinsam an.
            </p>
          </div>
        </div>

        {logs.length > 0 && (
          <div className="max-h-24 space-y-1 overflow-y-auto rounded border border-workshop-border/60 p-2">
            <div className="text-[10px] font-mono uppercase text-workshop-muted">Logs einbeziehen (optional)</div>
            {logs.slice(0, 12).map((log) => {
              const checked = selectedLogs.includes(log.name);
              return (
                <label key={log.name} className="flex cursor-pointer items-center gap-2 text-[11px]">
                  <input type="checkbox" checked={checked} onChange={() => toggleLog(log.name)} />
                  <span className="truncate text-workshop-text" title={log.name}>
                    {log.name}
                  </span>
                  {log.processed && <span className="text-workshop-muted">processed</span>}
                </label>
              );
            })}
          </div>
        )}

        <div
          ref={chatScrollRef}
          className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-md border border-workshop-border bg-black/20 p-3"
        >
          {messages.length === 0 && (
            <p className="text-xs text-workshop-muted">
              z.&nbsp;B. „Analysiere die letzten Logs und schlage Guidance für den 3D Builder vor“ oder „Erhöhe
              max_iterations auf 8 und erkläre warum“.
            </p>
          )}
          {messages.map((m, idx) => (
            <div
              key={`${m.role}-${idx}`}
              className={
                m.role === "user"
                  ? "ml-6 rounded-lg bg-workshop-accent/15 px-3 py-2 text-sm text-workshop-text"
                  : "mr-4 rounded-lg border border-workshop-border bg-workshop-bg px-3 py-2 text-xs text-workshop-text whitespace-pre-wrap"
              }
            >
              {m.content}
            </div>
          ))}
          {chatMutation.isPending && (
            <div className="text-xs text-workshop-muted">Meta-Coach analysiert…</div>
          )}
        </div>

        <form onSubmit={(e) => void handleSend(e)} className="flex flex-col gap-2">
          <textarea
            rows={3}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Frage oder Optimierungsauftrag an den Meta-Coach…"
            disabled={chatMutation.isPending}
            className="resize-none rounded-md border border-workshop-border bg-workshop-bg p-2 text-sm focus:border-workshop-accent focus:outline-none disabled:opacity-60"
          />
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={chatMutation.isPending || draft.trim().length < 2}
              className="rounded-md bg-workshop-accent px-4 py-2 text-sm font-semibold text-workshop-bg disabled:opacity-40"
            >
              {chatMutation.isPending ? "…" : "Senden"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
