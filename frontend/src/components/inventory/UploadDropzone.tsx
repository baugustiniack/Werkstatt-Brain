import { useRef, useState, type DragEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { AssetUploadResponse } from "../../api/types";

/** Drag-&-Drop-Upload inkl. Kamera-Capture – mit Abbrechen. */
export function UploadDropzone() {
  const [isDragOver, setIsDragOver] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [result, setResult] = useState<AssetUploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const queryClient = useQueryClient();

  const handleCancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsUploading(false);
  };

  const handleFiles = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    setError(null);
    setResult(null);
    setIsUploading(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      if (file.size > 25 * 1024 * 1024) {
        setError("Datei zu groß (max. 25 MB).");
        setIsUploading(false);
        return;
      }
      const form = new FormData();
      form.append("file", file);
      form.append("defer_process", "true");
      form.append("auto_process", "true");
      const data = await api.postForm<AssetUploadResponse>("/api/v1/inventory/upload", form, {
        signal: controller.signal,
      });
      setResult(data);
      queryClient.invalidateQueries({ queryKey: ["inventory-items"] });
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "Upload fehlgeschlagen");
      }
    } finally {
      setIsUploading(false);
      abortRef.current = null;
    }
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragOver(false);
    void handleFiles(event.dataTransfer.files);
  };

  return (
    <div className="flex flex-col gap-2">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
        onClick={() => !isUploading && fileInputRef.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed p-4 text-center text-xs transition ${
          isDragOver ? "border-workshop-accent bg-workshop-accent/10" : "border-workshop-border text-workshop-muted"
        }`}
      >
        <span>Datei hierher ziehen oder klicken</span>
        <span className="text-[10px]">Beliebige Formate (PDF, STEP, STL, Bilder, Text, …)</span>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={(event) => void handleFiles(event.target.files)}
        />
      </div>

      <button
        type="button"
        onClick={() => cameraInputRef.current?.click()}
        disabled={isUploading}
        className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-bg disabled:opacity-40"
      >
        📷 Foto aufnehmen (Fräser/Reststück)
      </button>
      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(event) => void handleFiles(event.target.files)}
      />

      {isUploading && (
        <div className="flex items-center justify-between text-xs">
          <span className="text-workshop-muted">Lädt hoch…</span>
          <button type="button" onClick={handleCancel} className="font-semibold text-workshop-danger hover:underline">
            Abbrechen
          </button>
        </div>
      )}
      {result && (
        <div className="rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs">
          <p className="text-workshop-success">
            {result.duplicate ? "Duplikat erkannt" : "Hochgeladen"} – Status: {result.status}
          </p>
        </div>
      )}
      {error && <p className="text-xs text-workshop-danger">{error}</p>}
    </div>
  );
}
