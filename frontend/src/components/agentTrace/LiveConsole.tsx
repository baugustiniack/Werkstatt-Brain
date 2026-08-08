import { useState } from "react";

import type { SandboxResult } from "../../api/types";
import type { AgentLogEntry } from "../../api/useCadStream";

const PY_KEYWORDS = /\b(with|import|from|as|def|return|if|else|for|in|None|True|False)\b/g;
const PY_STRINGS = /(".*?"|'.*?')/g;
const PY_COMMENTS = /(#.*)$/gm;

/** Sehr leichtgewichtiges Regex-basiertes Python-Highlighting (keine externe Lib nötig). */
function highlightPython(code: string): string {
  const escaped = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return escaped
    .replace(PY_COMMENTS, '<span class="text-workshop-muted">$1</span>')
    .replace(PY_STRINGS, '<span class="text-workshop-success">$1</span>')
    .replace(PY_KEYWORDS, '<span class="text-workshop-accent">$1</span>');
}

function summarizeLogEntry(entry: AgentLogEntry): string {
  const messages = entry.state.messages;
  if (Array.isArray(messages) && messages.length > 0) {
    const last = messages[messages.length - 1];
    const content = typeof last === "object" && last !== null && "content" in last ? String(last.content) : null;
    if (content) return content;
  }
  return `[${entry.node}] Node ausgeführt.`;
}

interface LiveConsoleProps {
  logs: AgentLogEntry[];
  generatedCode: string | null;
  sandboxResult: SandboxResult | null;
  /** Mehrteil-Fortschritt (Nutzer-Feedback: sichtbar machen, dass mehrere
   * Sub-Agenten nacheinander an mehreren Teilen arbeiten, statt es wie einen
   * einzigen Sprung zum fertigen Objekt wirken zu lassen). */
  currentPartIndex?: number;
  totalParts?: number;
  currentPartName?: string | null;
}

/** Einklappbares Terminal mit Node-Log, Code-Highlighting & Sandbox-Ausgabe (SPEC Kap. 5.2 Panel 2). */
export function LiveConsole({
  logs,
  generatedCode,
  sandboxResult,
  currentPartIndex = 0,
  totalParts = 0,
  currentPartName,
}: LiveConsoleProps) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-2 text-xs font-semibold text-workshop-muted hover:text-workshop-text"
        >
          <span>{expanded ? "▾" : "▸"}</span> Live Console
        </button>
        {totalParts > 0 && (
          <span className="rounded-full bg-workshop-accent/20 px-2 py-0.5 text-xs font-semibold text-workshop-accent">
            Teil {Math.min(currentPartIndex + 1, totalParts)}/{totalParts}
            {currentPartName ? `: ${currentPartName}` : ""}
          </span>
        )}
      </div>

      {expanded && (
        <div className="flex flex-col gap-2 rounded-md border border-workshop-border bg-black/40 p-3 font-mono text-xs">
          <div className="flex flex-col gap-1">
            {logs.length === 0 && <span className="text-workshop-muted">Warte auf Agenten-Aktivität…</span>}
            {logs.map((entry, idx) => (
              <div key={idx}>
                <span className="text-workshop-muted">[{new Date(entry.timestamp).toLocaleTimeString()}]</span>{" "}
                <span className="text-workshop-text">{summarizeLogEntry(entry)}</span>
              </div>
            ))}
          </div>

          {generatedCode && (
            <div className="mt-2 border-t border-workshop-border pt-2">
              <div className="mb-1 text-workshop-muted"># generated_code (build123d)</div>
              <pre
                className="overflow-x-auto whitespace-pre-wrap text-workshop-text"
                dangerouslySetInnerHTML={{ __html: highlightPython(generatedCode) }}
              />
            </div>
          )}

          {sandboxResult && (
            <div className="mt-2 border-t border-workshop-border pt-2">
              <div className="mb-1 text-workshop-muted"># sandbox_result</div>
              <div className={sandboxResult.status === "SUCCESS" ? "text-workshop-success" : "text-workshop-danger"}>
                status: {sandboxResult.status}
              </div>
              {sandboxResult.stdout && <pre className="whitespace-pre-wrap text-workshop-text">{sandboxResult.stdout}</pre>}
              {sandboxResult.traceback && (
                <pre className="whitespace-pre-wrap text-workshop-danger">{sandboxResult.traceback}</pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
