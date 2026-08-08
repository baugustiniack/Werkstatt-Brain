import { useRef, useState } from "react";

import { api } from "../../api/client";
import type { AssetUploadResponse } from "../../api/types";

const SUPPORTED_EXTENSIONS = ".step,.stp,.stl,.f3d,.png,.jpg,.jpeg";

interface ReferenceUploadProps {
  onAttach: (text: string) => void;
}

/** Referenzdatei für den Bauteilwunsch – mit Abbrechen während Upload. */
export function ReferenceUpload({ onAttach }: ReferenceUploadProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [attachedName, setAttachedName] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClear = () => {
    setAttachedName(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleCancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsUploading(false);
  };

  const handleFiles = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    setError(null);
    setIsUploading(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("title", `Referenz für Bauteilwunsch: ${file.name}`);
      const result = await api.postForm<AssetUploadResponse>("/api/v1/inventory/upload", form, {
        signal: controller.signal,
      });
      setAttachedName(file.name);
      const description = (result.vision_result?.description as string | undefined)?.trim();
      const text = description
        ? `[Referenzdatei "${file.name}"]: ${description}`
        : `[Referenzdatei "${file.name}" angehängt – keine automatische Analyse verfügbar]`;
      onAttach(text);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setError(null);
      } else {
        setError("Upload fehlgeschlagen oder abgebrochen.");
      }
    } finally {
      setIsUploading(false);
      abortRef.current = null;
    }
  };

  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-semibold text-workshop-muted">Referenzdatei (optional)</label>
      {attachedName ? (
        <div className="flex items-center justify-between gap-2 rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs">
          <span className="truncate text-workshop-text" title={attachedName}>
            📎 {attachedName}
          </span>
          <button type="button" onClick={handleClear} className="shrink-0 text-workshop-muted hover:text-workshop-danger">
            ✕
          </button>
        </div>
      ) : isUploading ? (
        <div className="flex items-center justify-between gap-2 rounded-md border border-workshop-border p-2 text-xs">
          <span className="text-workshop-muted">Lädt hoch…</span>
          <button type="button" onClick={handleCancel} className="font-semibold text-workshop-danger hover:underline">
            Abbrechen
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="flex items-center justify-center gap-2 rounded-md border-2 border-dashed border-workshop-border p-2.5 text-xs text-workshop-muted transition hover:border-workshop-accent hover:text-workshop-text"
        >
          📁 Foto/Skizze/CAD-Datei anhängen (STEP, STL, PNG, JPG)
        </button>
      )}
      <input
        ref={fileInputRef}
        type="file"
        accept={SUPPORTED_EXTENSIONS}
        className="hidden"
        onChange={(event) => void handleFiles(event.target.files)}
      />
      {error && <p className="text-xs text-workshop-danger">{error}</p>}
    </div>
  );
}
