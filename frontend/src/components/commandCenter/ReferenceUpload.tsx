import { useMemo, useRef, useState } from "react";

import { api } from "../../api/client";
import type { AssetUploadResponse, InventoryItem } from "../../api/types";
import { useInventoryItems } from "../../hooks/useInventoryItems";
import { assetFileUrl } from "../inventory/AssetPreview";

const SUPPORTED_EXTENSIONS = ".step,.stp,.stl,.f3d,.png,.jpg,.jpeg,.pdf";

interface ChatAttachmentsProps {
  onAttach: (text: string) => void;
}

type AttachedRef = {
  key: string;
  label: string;
};

function itemLabel(item: InventoryItem): string {
  return item.title?.trim() || item.file_name?.trim() || `Eintrag ${item.id.slice(0, 8)}`;
}

function itemDescription(item: InventoryItem): string {
  const user = (item.user_notes ?? "").trim();
  const ai = (item.ai_notes ?? item.notes ?? "").trim();
  if (user && ai) return `Nutzer: ${user}\n\nKI: ${ai}`;
  if (user) return user;
  if (ai) return ai;
  const vision = item.vision_result;
  if (vision && typeof vision.description === "string" && vision.description.trim()) {
    return vision.description.trim();
  }
  return "";
}

function formatInventoryAttach(item: InventoryItem): string {
  const name = itemLabel(item);
  const desc = itemDescription(item);
  const tags = (item.tags || []).filter((t) => !t.startsWith("conversation:")).slice(0, 6);
  const tagLine = tags.length ? ` Tags: ${tags.join(", ")}.` : "";
  const body = desc
    ? desc.slice(0, 1200)
    : "Keine Beschreibung hinterlegt – Datei/Eintrag als Referenz aus der Inventar-DB.";
  return `[Inventar-Referenz "${name}" | id=${item.id} | typ=${item.file_type}]${tagLine}\n${body}`;
}

/** Referenzdateien: Upload und/oder Auswahl aus der Inventar-DB (Mehrfach). */
export function ReferenceUpload({ onAttach }: ChatAttachmentsProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [attached, setAttached] = useState<AttachedRef[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const { data, isLoading, isFetching } = useInventoryItems({
    search: search.trim() || undefined,
  });
  const items = data?.items ?? [];

  const selectedItems = useMemo(
    () => items.filter((i) => selectedIds.has(i.id)),
    [items, selectedIds],
  );

  const pushAttached = (refs: AttachedRef[]) => {
    setAttached((prev) => {
      const keys = new Set(prev.map((p) => p.key));
      const next = [...prev];
      for (const r of refs) {
        if (!keys.has(r.key)) next.push(r);
      }
      return next;
    });
  };

  const removeAttached = (key: string) => {
    setAttached((prev) => prev.filter((a) => a.key !== key));
  };

  const handleCancelUpload = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsUploading(false);
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    setIsUploading(true);
    const controller = new AbortController();
    abortRef.current = controller;
    const chunks: string[] = [];
    const refs: AttachedRef[] = [];
    try {
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        form.append("title", `Referenz für Bauteilwunsch: ${file.name}`);
        const result = await api.postForm<AssetUploadResponse>("/api/v1/inventory/upload", form, {
          signal: controller.signal,
        });
        const description = (result.vision_result?.description as string | undefined)?.trim();
        chunks.push(
          description
            ? `[Referenzdatei "${file.name}"]: ${description}`
            : `[Referenzdatei "${file.name}" angehängt – keine automatische Analyse verfügbar]`,
        );
        refs.push({ key: `upload:${file.name}:${Date.now()}`, label: file.name });
      }
      pushAttached(refs);
      onAttach(chunks.join("\n\n"));
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setError(null);
      } else {
        setError("Upload fehlgeschlagen oder abgebrochen.");
      }
    } finally {
      setIsUploading(false);
      abortRef.current = null;
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const attachSelectedFromInventory = () => {
    if (selectedItems.length === 0) return;
    const chunks = selectedItems.map(formatInventoryAttach);
    const refs = selectedItems.map((item) => ({
      key: `inv:${item.id}`,
      label: itemLabel(item),
    }));
    pushAttached(refs);
    onAttach(chunks.join("\n\n"));
    setSelectedIds(new Set());
    setPickerOpen(false);
    setSearch("");
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-xs font-semibold text-workshop-muted">Referenzen (optional)</label>
        {attached.length > 0 && (
          <span className="text-[10px] text-workshop-muted">{attached.length} angehängt</span>
        )}
      </div>

      {attached.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {attached.map((a) => (
            <span
              key={a.key}
              className="inline-flex max-w-full items-center gap-1 rounded border border-workshop-border bg-workshop-bg px-2 py-0.5 text-[11px] text-workshop-text"
              title={a.label}
            >
              <span className="truncate">{a.label}</span>
              <button
                type="button"
                onClick={() => removeAttached(a.key)}
                className="shrink-0 text-workshop-muted hover:text-workshop-danger"
                aria-label="Entfernen"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {isUploading ? (
        <div className="flex items-center justify-between gap-2 rounded-md border border-workshop-border p-2 text-xs">
          <span className="text-workshop-muted">Lädt hoch…</span>
          <button type="button" onClick={handleCancelUpload} className="font-semibold text-workshop-danger hover:underline">
            Abbrechen
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="rounded-md border border-dashed border-workshop-border px-2.5 py-1.5 text-xs text-workshop-muted transition hover:border-workshop-accent hover:text-workshop-text"
          >
            Datei hochladen
          </button>
          <button
            type="button"
            onClick={() => setPickerOpen((v) => !v)}
            className={`rounded-md border px-2.5 py-1.5 text-xs font-semibold transition ${
              pickerOpen
                ? "border-workshop-accent bg-workshop-accent/15 text-workshop-text"
                : "border-workshop-border text-workshop-muted hover:border-workshop-accent hover:text-workshop-text"
            }`}
          >
            Aus Inventar wählen
          </button>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept={SUPPORTED_EXTENSIONS}
        multiple
        className="hidden"
        onChange={(event) => void handleFiles(event.target.files)}
      />

      {pickerOpen && (
        <div className="flex max-h-56 flex-col gap-2 rounded-md border border-workshop-border bg-workshop-bg/80 p-2">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Inventar durchsuchen…"
            className="w-full rounded border border-workshop-border bg-workshop-panel px-2 py-1.5 text-xs text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none"
          />
          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
            {(isLoading || isFetching) && items.length === 0 && (
              <p className="text-[11px] text-workshop-muted">Lade Inventar…</p>
            )}
            {!isLoading && items.length === 0 && (
              <p className="text-[11px] text-workshop-muted">Keine Einträge gefunden.</p>
            )}
            {items.slice(0, 40).map((item) => {
              const checked = selectedIds.has(item.id);
              const thumb = item.file_path ? assetFileUrl(item.id) : null;
              const isImage = item.file_type === "image";
              return (
                <label
                  key={item.id}
                  className={`flex cursor-pointer items-start gap-2 rounded-md px-1.5 py-1 text-xs ${
                    checked ? "bg-workshop-accent/15" : "hover:bg-workshop-panel"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleSelect(item.id)}
                    className="mt-1"
                  />
                  {thumb && isImage ? (
                    <img
                      src={thumb}
                      alt=""
                      className="h-8 w-8 shrink-0 rounded border border-workshop-border object-cover"
                    />
                  ) : (
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-workshop-border text-[9px] uppercase text-workshop-muted">
                      {item.file_type.slice(0, 3)}
                    </span>
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-workshop-text">{itemLabel(item)}</span>
                    <span className="block truncate text-[10px] text-workshop-muted">
                      {item.file_type}
                      {item.tags?.includes("KI-Generiert") ? " · KI-Generiert" : ""}
                      {itemDescription(item) ? ` · ${itemDescription(item).slice(0, 80)}` : ""}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
          <div className="flex items-center justify-between gap-2 border-t border-workshop-border pt-2">
            <span className="text-[10px] text-workshop-muted">
              {selectedIds.size > 0 ? `${selectedIds.size} ausgewählt` : "Mehrfachauswahl möglich"}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => {
                  setPickerOpen(false);
                  setSelectedIds(new Set());
                }}
                className="text-xs text-workshop-muted hover:underline"
              >
                Schließen
              </button>
              <button
                type="button"
                disabled={selectedIds.size === 0}
                onClick={attachSelectedFromInventory}
                className="rounded-md bg-workshop-accent px-2.5 py-1 text-xs font-semibold text-workshop-bg disabled:opacity-40"
              >
                Anhängen
              </button>
            </div>
          </div>
        </div>
      )}

      {error && <p className="text-xs text-workshop-danger">{error}</p>}
    </div>
  );
}
