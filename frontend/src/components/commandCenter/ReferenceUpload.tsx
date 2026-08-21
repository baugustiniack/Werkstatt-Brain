import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { api } from "../../api/client";
import type { AssetUploadResponse, InventoryItem } from "../../api/types";
import { useInventoryFolders, useInventoryItems } from "../../hooks/useInventoryItems";
import type { ReferenceMediaPreview } from "../modelViewer/ModelViewer";
import { assetFileUrl, MediaFilePreview, resolvePreviewKind } from "../inventory/AssetPreview";
import { compressImageForUpload, sleep } from "../../utils/compressImage";
import { useQueryClient } from "@tanstack/react-query";

const SUPPORTED_EXTENSIONS = ".step,.stp,.stl,.f3d,.png,.jpg,.jpeg,.webp,.heic,.pdf";
/** Max. Anhänge: Bilder werden komprimiert; Prompt bekommt nur IDs (keine Vision-Texte). */
const MAX_ATTACHMENTS = 7;
const MAX_FILE_BYTES = 8 * 1024 * 1024;
/** Kurze ID-Zeilen – Vision-Beschreibungen gehören in die Inventar-DB, nicht in den CAD-Prompt. */
const MAX_CONTEXT_CHARS = 2_500;

interface ChatAttachmentsProps {
  /** Kontexttext für den Agenten (nicht im Prompt-Feld anzeigen). */
  onContextChange: (context: string) => void;
  /** Großvorschau im Model-Viewer-Panel. */
  onPreviewChange?: (preview: ReferenceMediaPreview | null) => void;
  /** Wird nach dem Senden erhöht → Anhänge zurücksetzen. */
  clearToken?: number;
}

type AttachedRef = {
  key: string;
  label: string;
  context: string;
  previewUrl: string | null;
  fileType: string;
  itemId?: string;
  localObjectUrl?: string;
};

function itemLabel(item: InventoryItem): string {
  return item.title?.trim() || item.file_name?.trim() || `Eintrag ${item.id.slice(0, 8)}`;
}

/**
 * Nur Zeiger in die Inventar-DB – keine KI-Bildtexte.
 * Vision-Beschreibungen lädt der Inventory Manager aus `vision_result` / notes.
 * Konzept-Bilder nutzen die id=… für echte Bild-Referenzen.
 */
function formatInventoryAttach(item: InventoryItem): string {
  const name = itemLabel(item);
  return `[Inventar-Referenz "${name}" | id=${item.id} | typ=${item.file_type}]`;
}

function formatUploadAttach(label: string, assetId: string, fileType: string): string {
  return `[Referenzdatei "${label}" | id=${assetId} | typ=${fileType}]`;
}

function toPreview(item: InventoryItem): ReferenceMediaPreview | null {
  if (!item.file_path) return null;
  const kind = resolvePreviewKind(item.file_type, item.file_name);
  return {
    url: assetFileUrl(item.id),
    label: itemLabel(item),
    kind,
    fileType: item.file_type,
    itemId: item.id,
  };
}

/** Referenzdateien: Upload und/oder Auswahl aus der Inventar-DB (Mehrfach). */
export function ReferenceUpload({
  onContextChange,
  onPreviewChange,
  clearToken = 0,
}: ChatAttachmentsProps) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [attached, setAttached] = useState<AttachedRef[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");
  /** Zielordner für neue Chat-Uploads ("" = ohne Ordner). */
  const [uploadFolderId, setUploadFolderId] = useState("");
  /** Filter im Inventar-Picker: "all" | "unassigned" | folder UUID. */
  const [pickerFolderFilter, setPickerFolderFilter] = useState<"all" | "unassigned" | string>("all");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [focusedId, setFocusedId] = useState<string | null>(null);

  const { data: folders = [] } = useInventoryFolders();
  const pickerFilters = useMemo(() => {
    const base: { search?: string; folder_id?: string | null } = {
      search: search.trim() || undefined,
    };
    if (pickerFolderFilter === "unassigned") base.folder_id = null;
    else if (pickerFolderFilter !== "all") base.folder_id = pickerFolderFilter;
    return base;
  }, [search, pickerFolderFilter]);
  const { data, isLoading, isFetching } = useInventoryItems(pickerFilters);
  const items = data?.items ?? [];

  const folderNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const f of folders) map.set(f.id, f.name);
    return map;
  }, [folders]);

  const selectedItems = useMemo(
    () => items.filter((i) => selectedIds.has(i.id)),
    [items, selectedIds],
  );

  const focusedItem = useMemo(
    () => items.find((i) => i.id === focusedId) ?? null,
    [items, focusedId],
  );

  useEffect(() => {
    const joined = attached.map((a) => a.context).join("\n\n");
    onContextChange(joined.length > MAX_CONTEXT_CHARS ? joined.slice(0, MAX_CONTEXT_CHARS) + "\n…" : joined);
  }, [attached, onContextChange]);

  useEffect(() => {
    if (!onPreviewChange) return;
    if (focusedItem) {
      onPreviewChange(toPreview(focusedItem));
      return;
    }
    if (!pickerOpen && attached.length > 0) {
      const last = attached[attached.length - 1];
      if (last.previewUrl || last.itemId) {
        const kind = resolvePreviewKind(last.fileType, last.label);
        onPreviewChange({
          url: last.previewUrl || (last.itemId ? assetFileUrl(last.itemId) : ""),
          label: last.label,
          kind,
          fileType: last.fileType,
          itemId: last.itemId,
        });
        return;
      }
    }
  }, [focusedItem, attached, pickerOpen, onPreviewChange]);

  useEffect(() => {
    if (clearToken <= 0) return;
    setAttached((prev) => {
      for (const a of prev) {
        if (a.localObjectUrl) URL.revokeObjectURL(a.localObjectUrl);
      }
      return [];
    });
    setSelectedIds(new Set());
    setPickerOpen(false);
    setFocusedId(null);
    setError(null);
    onPreviewChange?.(null);
  }, [clearToken, onPreviewChange]);

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
    setAttached((prev) => {
      const victim = prev.find((a) => a.key === key);
      if (victim?.localObjectUrl) URL.revokeObjectURL(victim.localObjectUrl);
      return prev.filter((a) => a.key !== key);
    });
  };

  const handleCancelUpload = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsUploading(false);
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    const incoming = Array.from(files);
    const room = Math.max(0, MAX_ATTACHMENTS - attached.length);
    if (room === 0) {
      setError(`Maximal ${MAX_ATTACHMENTS} Anhänge pro Nachricht (Host-Schutz).`);
      return;
    }
    const batch = incoming.slice(0, room);
    if (incoming.length > room) {
      setError(`Nur ${room} weitere Anhänge möglich (Limit ${MAX_ATTACHMENTS}).`);
    }
    setIsUploading(true);
    const controller = new AbortController();
    abortRef.current = controller;
    const refs: AttachedRef[] = [];
    try {
      for (let i = 0; i < batch.length; i++) {
        const original = batch[i];
        // Pause zwischen Uploads → weniger RAM/CPU-Spitzen (BSOD-Schutz)
        if (i > 0) await sleep(400);

        let file = original;
        if (original.type.startsWith("image/")) {
          file = await compressImageForUpload(original);
        }
        if (file.size > MAX_FILE_BYTES) {
          setError(`„${original.name}“ ist nach Kompression noch zu groß (max. 8 MB) – übersprungen.`);
          continue;
        }

        // Kein Object-URL vom Original (volle Handy-Fotos) – nur Server-Vorschau
        const form = new FormData();
        form.append("file", file);
        form.append("title", `Referenz für Bauteilwunsch: ${original.name}`);
        form.append("defer_process", "true");
        // Keine Sofort-Vision-Kette für Chat-Anhänge (RAM-Schutz); Konzept nutzt Bild-IDs
        form.append("auto_process", "false");
        if (uploadFolderId) form.append("folder_id", uploadFolderId);
        const result = await api.postForm<AssetUploadResponse>("/api/v1/inventory/upload", form, {
          signal: controller.signal,
        });
        const context = formatUploadAttach(original.name, result.asset_id, result.file_type || "other");
        const kind = resolvePreviewKind(result.file_type || "other", original.name);
        const previewUrl = kind === "image" ? assetFileUrl(result.asset_id) : null;
        refs.push({
          key: `upload:${result.asset_id}`,
          label: original.name,
          context,
          previewUrl,
          fileType: result.file_type || "other",
          itemId: result.asset_id,
        });
        if (previewUrl) {
          onPreviewChange?.({
            url: previewUrl,
            label: original.name,
            kind,
            fileType: result.file_type,
            itemId: result.asset_id,
          });
        }
      }
      pushAttached(refs);
      queryClient.invalidateQueries({ queryKey: ["inventory-items"] });
      queryClient.invalidateQueries({ queryKey: ["inventory-folders"] });
    } catch (err) {
      for (const r of refs) {
        if (r.localObjectUrl) URL.revokeObjectURL(r.localObjectUrl);
      }
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

  const focusItem = (item: InventoryItem) => {
    setFocusedId(item.id);
    const preview = toPreview(item);
    if (preview) onPreviewChange?.(preview);
  };

  const attachSelectedFromInventory = () => {
    if (selectedItems.length === 0) return;
    const room = Math.max(0, MAX_ATTACHMENTS - attached.length);
    if (room === 0) {
      setError(`Maximal ${MAX_ATTACHMENTS} Anhänge pro Nachricht (Host-Schutz).`);
      return;
    }
    const pick = selectedItems.slice(0, room);
    if (selectedItems.length > room) {
      setError(`Nur ${room} weitere Anhänge möglich (Limit ${MAX_ATTACHMENTS}).`);
    }
    const refs = pick.map((item) => ({
      key: `inv:${item.id}`,
      label: itemLabel(item),
      context: formatInventoryAttach(item),
      previewUrl: item.file_path ? assetFileUrl(item.id) : null,
      fileType: item.file_type,
      itemId: item.id,
    }));
    pushAttached(refs);
    const last = pick[pick.length - 1];
    const preview = toPreview(last);
    if (preview) onPreviewChange?.(preview);
    setSelectedIds(new Set());
    setPickerOpen(false);
    setFocusedId(null);
    setSearch("");
  };

  const closePicker = () => {
    setPickerOpen(false);
    setSelectedIds(new Set());
    setFocusedId(null);
  };

  const pickerModal =
    pickerOpen &&
    createPortal(
      <div
        className="fixed inset-0 z-[100] flex items-stretch justify-center bg-black/80 p-2 sm:p-4"
        role="dialog"
        aria-modal="true"
        aria-label="Inventar auswählen"
        onClick={closePicker}
      >
        <div
          className="flex h-full w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-workshop-border bg-workshop-bg shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-workshop-border px-4 py-3">
            <div className="min-w-0 flex-1">
              <h2 className="text-base font-semibold text-workshop-text">Inventar – große Vorschau</h2>
              <p className="text-xs text-workshop-muted">
                Eintrag tippen → Bild rechts im Model Viewer · Checkbox = Mehrfachauswahl
              </p>
            </div>
            <select
              value={pickerFolderFilter}
              onChange={(e) => {
                setPickerFolderFilter(e.target.value);
                setFocusedId(null);
                setSelectedIds(new Set());
              }}
              className="rounded-md border border-workshop-border bg-workshop-panel px-2 py-2 text-sm text-workshop-text focus:border-workshop-accent focus:outline-none"
              aria-label="Ordner filtern"
            >
              <option value="all">Alle Ordner</option>
              <option value="unassigned">Ohne Ordner</option>
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {folder.name}
                  {folder.item_count > 0 ? ` (${folder.item_count})` : ""}
                </option>
              ))}
            </select>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Suchen…"
              className="w-full max-w-xs rounded-md border border-workshop-border bg-workshop-panel px-3 py-2 text-sm text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none sm:w-56"
            />
            <button
              type="button"
              onClick={closePicker}
              className="rounded-md border border-workshop-border px-3 py-2 text-sm text-workshop-muted hover:text-workshop-text"
            >
              Schließen
            </button>
          </div>

          <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_minmax(320px,42%)]">
            <div className="min-h-0 overflow-y-auto border-b border-workshop-border p-3 lg:border-b-0 lg:border-r">
              {(isLoading || isFetching) && items.length === 0 && (
                <p className="text-sm text-workshop-muted">Lade Inventar…</p>
              )}
              {!isLoading && items.length === 0 && (
                <p className="text-sm text-workshop-muted">Keine Einträge gefunden.</p>
              )}
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
                {items.slice(0, 80).map((item) => {
                  const checked = selectedIds.has(item.id);
                  const focused = focusedId === item.id;
                  const kind = resolvePreviewKind(item.file_type, item.file_name);
                  return (
                    <div
                      key={item.id}
                      className={`overflow-hidden rounded-lg border transition ${
                        focused
                          ? "border-workshop-accent ring-2 ring-workshop-accent/60"
                          : checked
                            ? "border-workshop-accent/70"
                            : "border-workshop-border"
                      }`}
                    >
                      <button
                        type="button"
                        className="relative block w-full bg-black/35"
                        onClick={() => focusItem(item)}
                      >
                        {item.file_path ? (
                          <div className="pointer-events-none">
                            <MediaFilePreview
                              itemId={item.id}
                              fileType={item.file_type}
                              fileName={item.file_name ?? itemLabel(item)}
                              heightClass="h-40 sm:h-44"
                              eager3d={focused && (kind === "stl" || kind === "pdf")}
                            />
                          </div>
                        ) : (
                          <div className="flex h-40 w-full flex-col items-center justify-center gap-1 sm:h-44">
                            <span className="text-xs font-semibold uppercase text-workshop-muted">
                              {item.file_type}
                            </span>
                          </div>
                        )}
                        {checked && (
                          <span className="absolute right-1.5 top-1.5 rounded bg-workshop-accent px-1.5 py-0.5 text-[10px] font-semibold text-workshop-bg">
                            ✓
                          </span>
                        )}
                      </button>
                      <label className="flex cursor-pointer items-center gap-2 border-t border-workshop-border px-2 py-2">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => {
                            toggleSelect(item.id);
                            focusItem(item);
                          }}
                          className="h-4 w-4"
                        />
                        <span className="min-w-0 flex-1 truncate text-[11px] text-workshop-text">
                          {itemLabel(item)}
                        </span>
                        {item.folder_id && folderNameById.get(item.folder_id) && (
                          <span className="shrink-0 truncate text-[9px] text-workshop-muted">
                            {folderNameById.get(item.folder_id)}
                          </span>
                        )}
                      </label>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="flex min-h-[240px] flex-col bg-workshop-panel/30 p-3 lg:min-h-0">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-workshop-muted">
                Model-Viewer-Vorschau
              </div>
              <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-lg border border-workshop-border bg-black/50 p-2">
                {focusedItem?.file_path ? (
                  <div className="h-full w-full min-h-[220px]">
                    <MediaFilePreview
                      itemId={focusedItem.id}
                      fileType={focusedItem.file_type}
                      fileName={focusedItem.file_name ?? itemLabel(focusedItem)}
                      heightClass="h-full min-h-[220px]"
                      eager3d
                    />
                  </div>
                ) : (
                  <p className="px-4 text-center text-sm text-workshop-muted">
                    Tippe links auf ein Bild, PDF oder STL – hier und im Model Viewer erscheint die große Ansicht.
                  </p>
                )}
              </div>
              {focusedItem && (
                <button
                  type="button"
                  onClick={() => {
                    if (!selectedIds.has(focusedItem.id)) toggleSelect(focusedItem.id);
                  }}
                  className="mt-3 rounded-md border border-workshop-accent px-3 py-2 text-sm font-semibold text-workshop-accent hover:bg-workshop-accent/10"
                >
                  {selectedIds.has(focusedItem.id) ? "Bereits ausgewählt" : "Dieses auswählen"}
                </button>
              )}
            </div>
          </div>

          <div className="flex shrink-0 items-center justify-between gap-3 border-t border-workshop-border px-4 py-3">
            <span className="text-xs text-workshop-muted">
              {selectedIds.size > 0
                ? `${selectedIds.size} ausgewählt`
                : "Mehrere Checkboxen setzen, dann anhängen"}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={closePicker}
                className="rounded-md border border-workshop-border px-3 py-2 text-sm text-workshop-muted hover:text-workshop-text"
              >
                Abbrechen
              </button>
              <button
                type="button"
                disabled={selectedIds.size === 0}
                onClick={attachSelectedFromInventory}
                className="rounded-md bg-workshop-accent px-4 py-2 text-sm font-semibold text-workshop-bg disabled:opacity-40"
              >
                {selectedIds.size > 0 ? `${selectedIds.size} anhängen` : "Anhängen"}
              </button>
            </div>
          </div>
        </div>
      </div>,
      document.body,
    );

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-xs font-semibold text-workshop-muted">
          Referenzen (optional, max. {MAX_ATTACHMENTS}, Fotos werden komprimiert)
        </label>
        {attached.length > 0 && (
          <span className="text-[10px] text-workshop-muted">
            {attached.length} angehängt · nur IDs im Prompt (Beschreibungen bleiben in der Inventar-DB)
          </span>
        )}
      </div>

      {attached.length > 0 && (
        <div className="max-h-28 overflow-y-auto rounded-md border border-workshop-border bg-workshop-bg/50 p-1.5">
          <div className="grid grid-cols-4 gap-1.5 sm:grid-cols-5 md:grid-cols-6">
            {attached.map((a) => {
              const kind = resolvePreviewKind(a.fileType, a.label);
              return (
                <div
                  key={a.key}
                  className="group relative overflow-hidden rounded border border-workshop-border bg-workshop-panel"
                >
                  <button
                    type="button"
                    className="block w-full"
                    onClick={() => {
                      if (a.itemId || a.previewUrl) {
                        onPreviewChange?.({
                          url: a.previewUrl || (a.itemId ? assetFileUrl(a.itemId) : ""),
                          label: a.label,
                          kind,
                          fileType: a.fileType,
                          itemId: a.itemId,
                        });
                      }
                    }}
                    title={`${a.label} – im Model Viewer anzeigen`}
                  >
                    {kind === "image" && (a.previewUrl || a.itemId) ? (
                      <img
                        src={a.previewUrl || assetFileUrl(a.itemId!)}
                        alt={a.label}
                        className="h-14 w-full object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="flex h-14 w-full flex-col items-center justify-center gap-0.5 px-1">
                        <span className="text-[9px] font-bold uppercase tracking-wide text-workshop-accent">
                          {kind === "pdf" ? "PDF" : kind === "stl" ? "STL" : a.fileType.slice(0, 4)}
                        </span>
                        <span className="line-clamp-1 w-full text-center text-[9px] text-workshop-muted">
                          {a.label}
                        </span>
                      </div>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => removeAttached(a.key)}
                    className="absolute right-0.5 top-0.5 flex h-5 w-5 items-center justify-center rounded bg-black/70 text-xs text-white opacity-80 hover:bg-workshop-danger hover:opacity-100"
                    aria-label="Entfernen"
                  >
                    ×
                  </button>
                </div>
              );
            })}
          </div>
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
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-[11px] text-workshop-muted">
            <span className="shrink-0">Ordner</span>
            <select
              value={uploadFolderId}
              onChange={(e) => setUploadFolderId(e.target.value)}
              className="max-w-[10rem] rounded-md border border-workshop-border bg-workshop-bg px-1.5 py-1 text-xs text-workshop-text focus:border-workshop-accent focus:outline-none"
              title="Neue Uploads landen in diesem Inventar-Ordner"
            >
              <option value="">Ohne Ordner</option>
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {folder.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="rounded-md border border-dashed border-workshop-border px-2.5 py-1.5 text-xs text-workshop-muted transition hover:border-workshop-accent hover:text-workshop-text"
          >
            Dateien hochladen
          </button>
          <button
            type="button"
            onClick={() => setPickerOpen(true)}
            className="rounded-md border border-workshop-border px-2.5 py-1.5 text-xs font-semibold text-workshop-muted transition hover:border-workshop-accent hover:text-workshop-text"
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

      {error && <p className="text-xs text-workshop-danger">{error}</p>}
      {pickerModal}
    </div>
  );
}
