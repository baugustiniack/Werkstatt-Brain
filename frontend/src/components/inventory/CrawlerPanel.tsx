import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { AssetUploadResponse } from "../../api/types";
import { useTriggerCrawlerScan } from "../../hooks/useCrawlerQueue";

const SUPPORTED_EXTENSIONS: string[] | null = null; // null = alle Formate
const UPLOAD_CONCURRENCY = 3;

interface UploadProgress {
  total: number;
  done: number;
  newAssets: number;
  duplicates: number;
  errors: number;
  cancelled?: boolean;
}

function isSupportedFile(file: File): boolean {
  if (!SUPPORTED_EXTENSIONS) return true;
  const name = file.name.toLowerCase();
  return SUPPORTED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

const EMPTY_PROGRESS: UploadProgress = { total: 0, done: 0, newAssets: 0, duplicates: 0, errors: 0 };

async function uploadFilesWithConcurrency(
  files: File[],
  onProgress: (updater: (prev: UploadProgress | null) => UploadProgress) => void,
  signal: AbortSignal,
) {
  let cursor = 0;
  const runWorker = async () => {
    while (cursor < files.length) {
      if (signal.aborted) return;
      const file = files[cursor];
      cursor += 1;
      try {
        const form = new FormData();
        form.append("file", file);
        const result = await api.postForm<AssetUploadResponse>("/api/v1/inventory/upload", form, { signal });
        onProgress((prev) => {
          const base = prev ?? EMPTY_PROGRESS;
          return {
            ...base,
            done: base.done + 1,
            newAssets: base.newAssets + (result.duplicate ? 0 : 1),
            duplicates: base.duplicates + (result.duplicate ? 1 : 0),
          };
        });
      } catch (err) {
        if (signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
        onProgress((prev) => {
          const base = prev ?? EMPTY_PROGRESS;
          return { ...base, done: base.done + 1, errors: base.errors + 1 };
        });
      }
    }
  };

  await Promise.all(Array.from({ length: Math.min(UPLOAD_CONCURRENCY, files.length) }, () => runWorker()));
}

export function CrawlerPanel() {
  const folderInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const scan = useTriggerCrawlerScan();
  const queryClient = useQueryClient();

  const handleCancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsUploading(false);
    setProgress((prev) => (prev ? { ...prev, cancelled: true } : prev));
  };

  const handleFolderSelected = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList).filter(isSupportedFile);

    if (files.length === 0) {
      setProgress({ total: 0, done: 0, newAssets: 0, duplicates: 0, errors: 0 });
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setIsUploading(true);
    setProgress({ total: files.length, done: 0, newAssets: 0, duplicates: 0, errors: 0 });
    await uploadFilesWithConcurrency(files, setProgress, controller.signal);
    setIsUploading(false);
    abortRef.current = null;
    queryClient.invalidateQueries({ queryKey: ["inventory-items"] });
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-workshop-muted">Ordner vom eigenen PC durchsuchen &amp; hochladen</span>
        {isUploading ? (
          <button
            type="button"
            onClick={handleCancel}
            className="shrink-0 rounded-md border border-workshop-danger px-3 py-1 text-xs font-semibold text-workshop-danger hover:bg-workshop-danger/10"
          >
            Abbrechen
          </button>
        ) : (
          <button
            type="button"
            onClick={() => folderInputRef.current?.click()}
            className="shrink-0 rounded-md border border-workshop-border px-3 py-1 text-xs font-semibold text-workshop-text hover:bg-workshop-bg"
          >
            🔍 Scan jetzt
          </button>
        )}
        <input
          ref={folderInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => void handleFolderSelected(event.target.files)}
          {...({ webkitdirectory: "true", directory: "" } as unknown as Record<string, string>)}
        />
      </div>

      {progress && (
        <p className="text-xs text-workshop-muted">
          {progress.done}/{progress.total} hochgeladen · {progress.newAssets} neu · {progress.duplicates} Duplikate
          {progress.errors > 0 && <span className="text-workshop-danger"> · {progress.errors} Fehler</span>}
          {progress.cancelled && <span className="text-workshop-warning"> · abgebrochen</span>}
        </p>
      )}

      <details className="text-xs text-workshop-muted">
        <summary className="cursor-pointer select-none hover:text-workshop-text">
          Erweitert: Server-Pfad scannen
        </summary>
        <div className="mt-2 flex flex-col gap-1">
          <p>
            Durchsucht einen auf dem Server/Container konfigurierten Pfad (nicht den lokalen PC im Browser) – nützlich
            für einen dauerhaft gemounteten Werkstatt-Ordner.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => scan.mutate()}
              disabled={scan.isPending}
              className="self-start rounded-md border border-workshop-border px-3 py-1 text-xs font-semibold text-workshop-text hover:bg-workshop-bg disabled:opacity-40"
            >
              {scan.isPending ? "Scanne…" : "Server-Pfad scannen"}
            </button>
            {scan.isPending && (
              <button
                type="button"
                onClick={() => scan.reset()}
                className="text-xs font-semibold text-workshop-danger hover:underline"
              >
                Abbrechen
              </button>
            )}
          </div>
          {scan.isSuccess && (
            <p className="text-workshop-success">
              {scan.data.new_assets} neue Assets, {scan.data.duplicates_skipped} Duplikate übersprungen.
            </p>
          )}
        </div>
      </details>
    </div>
  );
}
