import { useEffect, useMemo, useState } from "react";

import { api, apiBaseUrl } from "../../api/client";
import {
  useConversation,
  useConversations,
  type ConversationArtifact,
} from "../../hooks/useConversations";
import { useMetaCoachLogs, type MetaCoachLogSummary } from "../../hooks/useMetaCoach";

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
  | { source: "artifact"; id: string; label: string; subtitle: string; url: string }
  | { source: "agent_logs"; id: string; label: string; subtitle: string; name: string };

function artifactEntry(art: ConversationArtifact): LogEntry {
  const stamp = formatStamp(art.created_at);
  const session = art.cad_session_id ? art.cad_session_id.slice(0, 8) : "—";
  return {
    source: "artifact",
    id: `art:${art.id}`,
    label: art.label ?? "Agent-Transcript",
    subtitle: `${stamp} · Session ${session}…`,
    url: art.url,
  };
}

function agentLogEntry(log: MetaCoachLogSummary): LogEntry {
  return {
    source: "agent_logs",
    id: `file:${log.name}`,
    label: log.name,
    subtitle: `${formatStamp(log.modified_at)}${log.processed ? " · processed" : ""}`,
    name: log.name,
  };
}

/** Reiter Logging: Chat-Transcripts + Fallback aus agent_logs (Session-Match). */
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

    // Fallback: .txt aus agent_logs, die zur Session dieser Unterhaltung passen
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

    // Deduplizieren: wenn Artefakt denselben Dateinamen hat, File-Fallback weglassen
    const artLabels = new Set(arts.map((a) => a.label));
    const filesOnly = matchedFiles.filter((f) => !artLabels.has(f.label));

    return [...arts, ...filesOnly];
  }, [detail?.artifacts, allAgentLogs, sessionIds]);

  // Aktiven Chat / erste Unterhaltung vorauswählen
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

  return (
    <div className="flex h-full min-h-0 gap-3">
      <aside className="flex w-48 shrink-0 flex-col gap-2 border-r border-workshop-border pr-3 sm:w-56">
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

      <aside className="flex w-52 shrink-0 flex-col gap-2 border-r border-workshop-border pr-3 sm:w-64">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-mono uppercase tracking-wider text-workshop-muted">Log-Dateien</span>
          {detailFetching && <span className="text-[10px] text-workshop-muted">…</span>}
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {selectedConvId && logEntries.length === 0 && !detailFetching && (
            <p className="text-xs text-workshop-muted">
              Für diese Unterhaltung liegt noch kein Agent-Transcript vor. Sobald Agenten laufen, erscheint
              das Log hier (auch während der Konzept-Freigabe).
            </p>
          )}
          {logEntries.map((t) => {
            const active = t.id === selectedLogId;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setSelectedLogId(t.id)}
                className={`w-full rounded-md px-2 py-1.5 text-left text-xs ${
                  active
                    ? "bg-workshop-accent/20 text-workshop-text"
                    : "text-workshop-muted hover:bg-workshop-bg hover:text-workshop-text"
                }`}
              >
                <div className="font-medium text-workshop-text">{t.label}</div>
                <div className="truncate text-[10px] text-workshop-muted">
                  {t.subtitle}
                  {t.source === "agent_logs" ? " · agent_logs" : ""}
                </div>
              </button>
            );
          })}
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-xs text-workshop-muted">
            {selectedEntry
              ? `Vollständiges Log · ${selectedEntry.label}`
              : "Kein Log ausgewählt"}
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
    </div>
  );
}
