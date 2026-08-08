import { useState } from "react";

import { useApiKeyStatus, useUpdateApiKeys } from "../../hooks/useSettings";
import type { LlmProviderChoice } from "../../api/types";

/**
 * Einstellungen-Modal: optionale Anthropic-, Cursor- und OpenAI-API-Keys.
 * Bildbeschreibungen in der Inventar-DB nutzen ausschließlich OpenAI Vision.
 */
export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const { data: status, isLoading } = useApiKeyStatus();
  const update = useUpdateApiKeys();
  const [anthropicKey, setAnthropicKey] = useState("");
  const [cursorKey, setCursorKey] = useState("");
  const [openaiKey, setOpenaiKey] = useState("");

  const saveAnthropic = () => {
    if (!anthropicKey.trim()) return;
    update.mutate({ anthropic_api_key: anthropicKey.trim() }, { onSuccess: () => setAnthropicKey("") });
  };

  const saveCursor = () => {
    if (!cursorKey.trim()) return;
    update.mutate({ cursor_api_key: cursorKey.trim() }, { onSuccess: () => setCursorKey("") });
  };

  const saveOpenai = () => {
    if (!openaiKey.trim()) return;
    update.mutate({ openai_api_key: openaiKey.trim() }, { onSuccess: () => setOpenaiKey("") });
  };

  const setProvider = (llm_provider: LlmProviderChoice) => {
    update.mutate({ llm_provider });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-lg border border-workshop-border bg-workshop-panel p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-workshop-text">⚙ Einstellungen</h3>
          <button type="button" onClick={onClose} className="text-workshop-muted hover:text-workshop-text">
            ✕
          </button>
        </div>

        <p className="mb-4 text-xs text-workshop-muted">
          Keys sind optional. <strong className="text-workshop-text">Bilder &amp; Inventar-Fotos</strong> sowie
          Konzept-Fotos brauchen <strong className="text-workshop-text">OpenAI</strong>. Concept Builder &amp; 3D
          Builder: Anthropic oder Cursor. PDF/STL-Beschreibungen: Anthropic oder Cursor.
        </p>

        {/* Provider-Wahl */}
        <label className="mb-1 block text-xs font-semibold text-workshop-muted">Bevorzugter LLM-Provider</label>
        <div className="mb-4 flex flex-wrap gap-1">
          {(["auto", "anthropic", "cursor"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setProvider(option)}
              disabled={update.isPending}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
                status?.llm_provider === option
                  ? "bg-workshop-accent text-workshop-bg"
                  : "border border-workshop-border text-workshop-muted hover:bg-workshop-bg"
              }`}
            >
              {option === "auto" ? "Auto" : option === "anthropic" ? "Anthropic" : "Cursor"}
            </button>
          ))}
        </div>
        <p className="mb-4 text-[11px] text-workshop-muted">
          Aktiv:{" "}
          <span className="font-semibold text-workshop-text">
            {isLoading ? "…" : status?.active_provider === "none" ? "keiner (Heuristik)" : status?.active_provider}
          </span>
          {status?.llm_provider === "auto" && " · Auto wählt Anthropic vor Cursor"}
        </p>

        {/* Anthropic */}
        <label className="mb-1 block text-xs font-semibold text-workshop-muted">Anthropic (Claude) API-Key</label>
        <p className="mb-2 text-xs text-workshop-muted">
          Concept Builder &amp; 3D Builder (Text). Keine Inventar-Bildanalyse.
        </p>
        <div className="mb-2 flex items-center gap-2 text-xs">
          <span
            className={`h-2 w-2 rounded-full ${
              status?.anthropic_configured ? "bg-workshop-success" : "bg-workshop-muted"
            }`}
          />
          <span className="text-workshop-muted">
            {isLoading ? "Lädt…" : status?.anthropic_configured ? "Key hinterlegt" : "Kein Key hinterlegt"}
          </span>
        </div>
        <div className="mb-1 flex gap-2">
          <input
            type="password"
            value={anthropicKey}
            onChange={(event) => setAnthropicKey(event.target.value)}
            placeholder="sk-ant-…"
            className="flex-1 rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none"
          />
          <button
            type="button"
            onClick={saveAnthropic}
            disabled={update.isPending || !anthropicKey.trim()}
            className="rounded-md bg-workshop-accent px-3 py-2 text-xs font-semibold text-workshop-bg disabled:opacity-40"
          >
            Speichern
          </button>
        </div>
        {status?.anthropic_configured && (
          <button
            type="button"
            onClick={() => update.mutate({ anthropic_api_key: "" })}
            disabled={update.isPending}
            className="mb-4 text-xs text-workshop-danger hover:underline"
          >
            Anthropic-Key entfernen
          </button>
        )}
        {!status?.anthropic_configured && <div className="mb-4" />}

        {/* Cursor */}
        <label className="mb-1 block text-xs font-semibold text-workshop-muted">Cursor API-Key</label>
        <p className="mb-2 text-xs text-workshop-muted">
          Concept Builder &amp; 3D Builder über Cursor SDK (Composer). Keine Bildgenerierung – Fotos brauchen OpenAI.
        </p>
        <div className="mb-2 flex items-center gap-2 text-xs">
          <span
            className={`h-2 w-2 rounded-full ${status?.cursor_configured ? "bg-workshop-success" : "bg-workshop-muted"}`}
          />
          <span className="text-workshop-muted">
            {isLoading ? "Lädt…" : status?.cursor_configured ? "Key hinterlegt" : "Kein Key hinterlegt"}
          </span>
        </div>
        <div className="mb-1 flex gap-2">
          <input
            type="password"
            value={cursorKey}
            onChange={(event) => setCursorKey(event.target.value)}
            placeholder="key_…"
            className="flex-1 rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none"
          />
          <button
            type="button"
            onClick={saveCursor}
            disabled={update.isPending || !cursorKey.trim()}
            className="rounded-md bg-workshop-accent px-3 py-2 text-xs font-semibold text-workshop-bg disabled:opacity-40"
          >
            Speichern
          </button>
        </div>
        {status?.cursor_configured && (
          <button
            type="button"
            onClick={() => update.mutate({ cursor_api_key: "" })}
            disabled={update.isPending}
            className="mb-4 text-xs text-workshop-danger hover:underline"
          >
            Cursor-Key entfernen
          </button>
        )}
        {!status?.cursor_configured && <div className="mb-4" />}

        {/* OpenAI – Konzept-Fotos */}
        <label className="mb-1 block text-xs font-semibold text-workshop-muted">OpenAI API-Key</label>
        <p className="mb-2 text-xs text-workshop-muted">
          Inventar-Bildbeschreibungen (Vision) und fotorealistische Konzept-Fotos (Images API).
        </p>
        <div className="mb-2 flex items-center gap-2 text-xs">
          <span
            className={`h-2 w-2 rounded-full ${status?.openai_configured ? "bg-workshop-success" : "bg-workshop-muted"}`}
          />
          <span className="text-workshop-muted">
            {isLoading ? "Lädt…" : status?.openai_configured ? "Key hinterlegt" : "Kein Key hinterlegt"}
          </span>
        </div>
        <div className="mb-1 flex gap-2">
          <input
            type="password"
            value={openaiKey}
            onChange={(event) => setOpenaiKey(event.target.value)}
            placeholder="sk-…"
            className="flex-1 rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none"
          />
          <button
            type="button"
            onClick={saveOpenai}
            disabled={update.isPending || !openaiKey.trim()}
            className="rounded-md bg-workshop-accent px-3 py-2 text-xs font-semibold text-workshop-bg disabled:opacity-40"
          >
            Speichern
          </button>
        </div>
        {status?.openai_configured && (
          <button
            type="button"
            onClick={() => update.mutate({ openai_api_key: "" })}
            disabled={update.isPending}
            className="text-xs text-workshop-danger hover:underline"
          >
            OpenAI-Key entfernen
          </button>
        )}
      </div>
    </div>
  );
}
