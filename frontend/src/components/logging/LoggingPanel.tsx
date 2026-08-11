import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { api, apiBaseUrl } from "../../api/client";
import {
  useConversation,
  useConversations,
  type ConversationArtifact,
} from "../../hooks/useConversations";
import {
  useMetaCoachChat,
  useMetaCoachLogs,
  type MetaCoachChatMessage,
  type MetaCoachLogSummary,
} from "../../hooks/useMetaCoach";

function formatStamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString("de-DE", {
      dateStyle: "short",
      timeStyle: "medium",
    });
  } catch {
    return iso;
  }
}

type LogEntry =
  | { source: "artifact"; id: string; label: string; subtitle: string; url: string; coachName: string | null }
  | { source: "agent_logs"; id: string; label: string; subtitle: string; name: string; coachName: string };

function coachNameFromLabel(label: string): string | null {
  const base = label.trim().split(/[/\\]/).pop() ?? label.trim();
  return base.toLowerCase().endsWith(".txt") ? base : null;
}

function artifactEntry(art: ConversationArtifact): LogEntry {
  const stamp = formatStamp(art.created_at);
  const session = art.cad_session_id ? art.cad_session_id.slice(0, 8) : "—";
  const label = art.label ?? "Agent-Transcript";
  return {
    source: "artifact",
    id: `art:${art.id}`,
    label,
    subtitle: `${stamp} · Session ${session}…`,
    url: art.url,
    coachName: coachNameFromLabel(label),
  };
}

function agentLogEntry(log: MetaCoachLogSummary): LogEntry {
  return {
    source: "agent_logs",
    id: `file:${log.name}`,
    label: log.name,
    subtitle: `${formatStamp(log.modified_at)}${log.processed ? " · processed" : ""}`,
    name: log.name,
    coachName: log.name,
  };
}

function MetaCoachSidebar({
  selectedLogNames,
}: {
  selectedLogNames: string[];
}) {
  const chatMutation = useMetaCoachChat();
  const [messages, setMessages] = useState<MetaCoachChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, chatMutation.isPending]);

  const handleSend = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || chatMutation.isPending) return;
    const next: MetaCoachChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setDraft("");
    try {
      const result = await chatMutation.mutateAsync({
        messages: next,
        log_names: selectedLogNames.length ? selectedLogNames : undefined,
      });
      setMessages((prev) => [...prev, { role: "assistant", content: result.reply }]);
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
    <div className="flex min-h-0 flex-1 flex-col gap-2 border-t border-workshop-border pt-2 lg:border-l lg:border-t-0 lg:pl-3 lg:pt-0">
      <div>
        <div className="text-[10px] font-mono uppercase tracking-wider text-workshop-accent">Meta-Coach</div>
        <p className="mt-1 text-[11px] text-workshop-muted">
          Analysiert die in „Log-Dateien“ angehakten Logs. Kein Direct Write – Änderungen im Reiter „Agent
          Workflow“.
        </p>
        <p className="mt-1 text-[10px] text-workshop-muted">
          {selectedLogNames.length === 0
            ? "Keine Logs ausgewählt – es werden die neuesten Logs genutzt."
            : `${selectedLogNames.length} Log(s) für die Analyse ausgewählt.`}
        </p>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-md border border-workshop-border bg-black/20 p-2">
        {messages.length === 0 && (
          <p className="text-[11px] text-workshop-muted">
            Frage z. B.: „Welche Guidance sollte der 3D Builder aus den letzten Läufen bekommen?“
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={`${m.role}-${i}`}
            className={`rounded px-2 py-1.5 text-[11px] whitespace-pre-wrap ${
              m.role === "user"
                ? "bg-workshop-accent/15 text-workshop-text"
                : "bg-workshop-bg/60 text-workshop-text"
            }`}
          >
            <span className="mb-0.5 block text-[9px] font-mono uppercase text-workshop-muted">{m.role}</span>
            {m.content}
          </div>
        ))}
        {chatMutation.isPending && (
          <p className="text-[11px] text-workshop-muted animate-pulse">Meta-Coach analysiert…</p>
        )}
      </div>

      <form onSubmit={handleSend} className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Empfehlung anfragen…"
          className="min-w-0 flex-1 rounded-md border border-workshop-border bg-workshop-bg px-2 py-1.5 text-xs"
        />
        <button
          type="submit"
          disabled={!draft.trim() || chatMutation.isPending}
          className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
        >
          Senden
        </button>
      </form>
    </div>
  );
}

/** Reiter Logging: Transcripts + Meta-Coach (Empfehlungen aus Logs). */
export function LoggingPanel({
  preferredConversationId,
}: {
  preferredConversationId?: string | null;
}) {
  const { data: conversations = [], isLoading: listLoading } = useConversations();
  const { data: allAgentLogs = [] } = useMetaCoachLogs();
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);
  const [logText, setLogText] = useState<string | null>(null);
  const [logError, setLogError] = useState<string | null>(null);
  const [loadingText, setLoadingText] = useState(false);
  const [coachLogNames, setCoachLogNames] = useState<string[]>([]);

  const { data: detail, isFetching: detailFetching } = useConversation(selectedConvId);

  const sessionIds = useMemo(() => {
    const ids = new Set<string>();
    for (const m of detail?.messages ?? []) {
      if (m.cad_session_id) ids.add(m.cad_session_id);
    }
    for (const a of detail?.artifacts ?? []) {
      if (a.cad_session_id) ids.add(a.cad_session_id);
    }
    return ids;
  }, [detail?.messages, detail?.artifacts]);

  const logEntries = useMemo(() => {
    const arts = (detail?.artifacts ?? [])
      .filter((a) => a.kind === "transcript")
      .slice()
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .map(artifactEntry);

    const matchedFiles = allAgentLogs
      .filter((log) => {
        if (sessionIds.size === 0) return false;
        const name = log.name.toLowerCase();
        return [...sessionIds].some((sid) => {
          const safe = sid.replace(/[^a-zA-Z0-9_-]/g, "_").toLowerCase().slice(0, 48);
          return name.includes(safe) || name.includes(sid.slice(0, 8).toLowerCase());
        });
      })
      .map(agentLogEntry);

    const artLabels = new Set(arts.map((a) => a.label));
    const filesOnly = matchedFiles.filter((f) => !artLabels.has(f.label));

    return [...arts, ...filesOnly];
  }, [detail?.artifacts, allAgentLogs, sessionIds]);

  const selectableCoachNames = useMemo(() => {
    const fromList = logEntries.map((e) => e.coachName).filter((n): n is string => Boolean(n));
    const fromAll = allAgentLogs.map((l) => l.name);
    return [...new Set([...fromList, ...fromAll])];
  }, [logEntries, allAgentLogs]);

  useEffect(() => {
    if (preferredConversationId && conversations.some((c) => c.id === preferredConversationId)) {
      setSelectedConvId(preferredConversationId);
      return;
    }
    setSelectedConvId((prev) => prev ?? (conversations[0]?.id ?? null));
  }, [conversations, preferredConversationId]);

  useEffect(() => {
    setSelectedLogId(null);
    setLogText(null);
    setLogError(null);
  }, [selectedConvId]);

  useEffect(() => {
    if (!selectedLogId && logEntries.length > 0) {
      setSelectedLogId(logEntries[0].id);
    }
  }, [logEntries, selectedLogId]);

  useEffect(() => {
    if (!selectedLogId) {
      setLogText(null);
      return;
    }
    const entry = logEntries.find((e) => e.id === selectedLogId);
    if (!entry) {
      setLogText(null);
      return;
    }
    let cancelled = false;
    setLoadingText(true);
    setLogError(null);

    const load = async () => {
      if (entry.source === "artifact") {
        const res = await fetch(`${apiBaseUrl()}${entry.url}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      }
      const data = await api.get<{ name: string; content: string }>(
        `/api/v1/meta-coach/logs/${encodeURIComponent(entry.name)}`,
      );
      return data.content;
    };

    load()
      .then((text) => {
        if (!cancelled) setLogText(text);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLogText(null);
          setLogError(err instanceof Error ? err.message : "Log konnte nicht geladen werden.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingText(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedLogId, logEntries]);

  const selectedEntry = logEntries.find((e) => e.id === selectedLogId) ?? null;

  const toggleCoachLog = (name: string) => {
    setCoachLogNames((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]));
  };

  const selectAllLogs = () => {
    setCoachLogNames(selectableCoachNames);
  };

  const clearCoachLogs = () => {
    setCoachLogNames([]);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 lg:flex-row">
      <aside className="flex max-h-40 w-full shrink-0 flex-col gap-2 border-b border-workshop-border pb-2 sm:w-48 lg:max-h-none lg:w-52 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-3">
        <div className="text-[10px] font-mono uppercase tracking-wider text-workshop-muted">Unterhaltung</div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {listLoading && <p className="text-xs text-workshop-muted">Lade…</p>}
          {!listLoading && conversations.length === 0 && (
            <p className="text-xs text-workshop-muted">Noch keine Unterhaltungen.</p>
          )}
          {conversations.map((c) => {
            const active = c.id === selectedConvId;
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => setSelectedConvId(c.id)}
                className={`w-full truncate rounded-md px-2 py-1.5 text-left text-xs ${
                  active
                    ? "bg-workshop-accent/20 text-workshop-text"
                    : "text-workshop-muted hover:bg-workshop-bg hover:text-workshop-text"
                }`}
                title={c.title}
              >
                {c.title}
              </button>
            );
          })}
        </div>
      </aside>

      <aside className="flex max-h-40 w-full shrink-0 flex-col gap-2 border-b border-workshop-border pb-2 sm:w-56 lg:max-h-none lg:w-64 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-mono uppercase tracking-wider text-workshop-muted">Log-Dateien</span>
          {detailFetching && <span className="text-[10px] text-workshop-muted">…</span>}
        </div>
        <div className="flex flex-wrap gap-1">
          <button
            type="button"
            onClick={selectAllLogs}
            disabled={selectableCoachNames.length === 0}
            className="rounded border border-workshop-border px-2 py-0.5 text-[10px] font-semibold text-workshop-text hover:border-workshop-accent disabled:opacity-40"
            title="Alle verfügbaren Logs für den Meta-Coach einbeziehen"
          >
            Alle Logs
          </button>
          {coachLogNames.length > 0 && (
            <button
              type="button"
              onClick={clearCoachLogs}
              className="rounded border border-workshop-border px-2 py-0.5 text-[10px] text-workshop-muted hover:text-workshop-text"
            >
              Auswahl leeren
            </button>
          )}
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {selectedConvId && logEntries.length === 0 && !detailFetching && (
            <p className="text-xs text-workshop-muted">
              Für diese Unterhaltung liegt noch kein Agent-Transcript vor.
            </p>
          )}
          {logEntries.map((t) => {
            const active = t.id === selectedLogId;
            const coachName = t.coachName;
            const checked = Boolean(coachName && coachLogNames.includes(coachName));
            return (
              <div
                key={t.id}
                className={`flex items-start gap-1.5 rounded-md px-1.5 py-1.5 text-xs ${
                  active
                    ? "bg-workshop-accent/20 text-workshop-text"
                    : "text-workshop-muted hover:bg-workshop-bg hover:text-workshop-text"
                }`}
              >
                <input
                  type="checkbox"
                  className="mt-0.5 shrink-0"
                  checked={checked}
                  disabled={!coachName}
                  title={
                    coachName
                      ? "Für Meta-Coach-Analyse einbeziehen"
                      : "Dieses Log kann nicht an den Meta-Coach übergeben werden"
                  }
                  onChange={() => {
                    if (coachName) toggleCoachLog(coachName);
                  }}
                  onClick={(e) => e.stopPropagation()}
                />
                <button
                  type="button"
                  onClick={() => setSelectedLogId(t.id)}
                  className="min-w-0 flex-1 text-left"
                >
                  <div className="truncate font-medium text-workshop-text">{t.label}</div>
                  <div className="truncate text-[10px] text-workshop-muted">
                    {t.subtitle}
                    {t.source === "agent_logs" ? " · agent_logs" : ""}
                  </div>
                </button>
              </div>
            );
          })}
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2 lg:flex-row">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-xs text-workshop-muted">
              {selectedEntry ? `Vollständiges Log · ${selectedEntry.label}` : "Kein Log ausgewählt"}
            </span>
            {selectedEntry?.source === "artifact" && (
              <a
                href={`${apiBaseUrl()}${selectedEntry.url}`}
                download
                className="shrink-0 text-xs font-semibold text-workshop-accent hover:underline"
              >
                Download .txt
              </a>
            )}
          </div>
          <pre className="min-h-0 flex-1 overflow-auto rounded-md border border-workshop-border bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-workshop-text whitespace-pre-wrap">
            {loadingText && "Lade Log…"}
            {!loadingText && logError && <span className="text-workshop-danger">{logError}</span>}
            {!loadingText && !logError && logText}
            {!loadingText && !logError && !logText && (
              <span className="text-workshop-muted">Wähle links eine Unterhaltung und eine Log-Datei.</span>
            )}
          </pre>
        </div>

        <div className="flex min-h-[220px] w-full shrink-0 flex-col lg:w-80 xl:w-96">
          <MetaCoachSidebar selectedLogNames={coachLogNames} />
        </div>
      </div>
    </div>
  );
}
