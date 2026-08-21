import { useEffect, useMemo, useState, type ReactNode } from "react";

import type { InventoryFolder, InventoryItem } from "../../api/types";
import {
  useCreateInventoryFolder,
  useCreateManualEntry,
  useDeleteInventoryFolder,
  useDeleteInventoryItem,
  useInventoryFolders,
  useInventoryItems,
  useKnowledgeGraphStats,
  useProcessInventoryItem,
  useProcessPendingItems,
  useTrainKnowledgeGraph,
  useUpdateInventoryFolder,
  useUpdateInventoryItem,
} from "../../hooks/useInventoryItems";
import { useApiKeyStatus } from "../../hooks/useSettings";
import { CrawlerPanel } from "./CrawlerPanel";
import { AssetPreview, assetFileUrl } from "./AssetPreview";
import { UploadDropzone } from "./UploadDropzone";

const STATUS_COLORS: Record<string, string> = {
  pending: "text-workshop-warning",
  processing: "text-workshop-accent",
  indexed: "text-workshop-success",
  failed: "text-workshop-danger",
};

const SOURCE_LABELS: Record<string, string> = {
  crawler: "Crawler",
  upload: "Upload",
  manual: "Manuell",
};

function userNotesOf(item: InventoryItem): string {
  return (item.user_notes ?? "").trim();
}

function aiNotesOf(item: InventoryItem): string {
  const ai = (item.ai_notes ?? item.notes ?? "").trim();
  if (ai) return ai;
  const vision = item.vision_result;
  if (vision && typeof vision.description === "string" && vision.description.trim()) {
    return vision.description.trim();
  }
  return "";
}

function previewDescription(item: InventoryItem): string {
  return userNotesOf(item) || aiNotesOf(item);
}

const CATEGORY_LABELS: Record<string, string> = {
  tool: "Werkzeug",
  material: "Rohmaterial",
  product_concept: "Produktkonzept",
  reference_photo: "Referenzfoto",
  cad_reference: "CAD-Referenz",
  document: "Dokument",
  unknown: "Unklar",
};

function categoryOf(item: InventoryItem): string | null {
  const cat = item.vision_result?.category;
  if (typeof cat !== "string" || !cat) return null;
  return CATEGORY_LABELS[cat] ?? cat;
}

function displayTags(item: InventoryItem): string[] {
  const tags = item.tags ?? [];
  // conversation:uuid / interne Flags kompakt halten; KI-Markierung separat anzeigen
  return tags
    .filter(
      (t) =>
        !t.startsWith("conversation:") &&
        !t.startsWith("part:") &&
        t !== "from_chat" &&
        t !== "concept_image" &&
        t !== "generated_3d" &&
        t !== "cad_model" &&
        t !== "KI-Generiert",
    )
    .slice(0, 6);
}

function needsAiScan(item: InventoryItem): boolean {
  if (item.status === "pending" || item.status === "failed") return true;
  if (
    item.tags?.includes("heuristic_fallback") ||
    item.tags?.includes("cad_raw") ||
    item.tags?.includes("vision_error")
  ) {
    return true;
  }
  const text = aiNotesOf(item).toLowerCase();
  return (
    text.includes("kein vision-modell") ||
    text.includes("kein openai") ||
    text.includes("rohe cad-datei") ||
    text.includes("automatische heuristik") ||
    text.includes("bitte manuell prüfen") ||
    text.includes("bitte beschreibung manuell ergänzen") ||
    (text.includes("bildanalyse") && text.includes("fehlgeschlagen"))
  );
}

function AssetDetail({
  item,
  folders,
  onClose,
}: {
  item: InventoryItem;
  folders: InventoryFolder[];
  onClose: () => void;
}) {
  const update = useUpdateInventoryItem();
  const process = useProcessInventoryItem();
  const remove = useDeleteInventoryItem();
  const { data: keyStatus } = useApiKeyStatus();
  const [title, setTitle] = useState(item.title ?? "");
  const [userNotes, setUserNotes] = useState(userNotesOf(item));
  const [aiNotes, setAiNotes] = useState(aiNotesOf(item));
  const [folderId, setFolderId] = useState(item.folder_id ?? "");
  const [showMeta, setShowMeta] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileUrl = item.file_path ? assetFileUrl(item.id) : null;
  const dirty =
    title.trim() !== (item.title ?? "").trim() ||
    userNotes.trim() !== userNotesOf(item) ||
    aiNotes.trim() !== aiNotesOf(item) ||
    (folderId || null) !== (item.folder_id ?? null);
  const openaiReady = !!keyStatus?.openai_configured;
  const showImageKeyHint = item.file_type === "image" && !openaiReady;
  const showRescanHint = item.file_type === "image" && openaiReady && needsAiScan(item);

  useEffect(() => {
    setTitle(item.title ?? "");
    setUserNotes(userNotesOf(item));
    setAiNotes(aiNotesOf(item));
    setFolderId(item.folder_id ?? "");
    setShowMeta(false);
    setConfirmDelete(false);
  }, [item.id, item.title, item.user_notes, item.ai_notes, item.notes, item.vision_result, item.folder_id]);

  const handleSave = () => {
    update.mutate({
      id: item.id,
      title: title.trim() || undefined,
      user_notes: userNotes,
      ai_notes: aiNotes,
      folder_id: folderId || null,
    });
  };

  const handleDelete = () => {
    remove.mutate(item.id, {
      onSuccess: () => onClose(),
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="shrink-0 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-[10px] font-mono uppercase tracking-wider text-workshop-muted">Beschreibung</div>
            <div className="truncate text-sm font-semibold text-workshop-text">
              {item.title ?? item.file_name ?? "Eintrag"}
            </div>
          </div>
          <button type="button" onClick={onClose} className="text-xs text-workshop-muted hover:text-workshop-text">
            Schließen
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[11px] text-workshop-muted">
          <span>{SOURCE_LABELS[item.source] ?? item.source}</span>
          <span>·</span>
          <span>{item.file_type}</span>
          {categoryOf(item) && (
            <>
              <span>·</span>
              <span className="rounded border border-workshop-border px-1.5 py-0.5">{categoryOf(item)}</span>
            </>
          )}
          {displayTags(item).map((tag) => (
            <span
              key={tag}
              className={`rounded px-1.5 py-0.5 ${
                tag === "KI-Generiert"
                  ? "border border-workshop-accent/50 bg-workshop-accent/10 text-workshop-accent"
                  : "border border-workshop-border"
              }`}
            >
              {tag}
            </span>
          ))}
          <span className={`ml-auto font-mono ${STATUS_COLORS[item.status] ?? ""}`}>{item.status}</span>
        </div>

        {item.file_path && (
          <AssetPreview itemId={item.id} fileType={item.file_type} fileName={item.file_name ?? item.title} />
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-0.5">
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-workshop-muted">Ordner</span>
        <select
          value={folderId}
          onChange={(e) => setFolderId(e.target.value)}
          className="rounded-md border border-workshop-border bg-workshop-bg px-2 py-1.5"
        >
          <option value="">Ohne Ordner</option>
          {folders.map((folder) => (
            <option key={folder.id} value={folder.id}>
              {folder.name}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-workshop-muted">Titel</span>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="rounded-md border border-workshop-border bg-workshop-bg px-2 py-1.5"
        />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-workshop-muted">
          Deine Beschreibung (wird bei „KI beschreiben“ einbezogen, nie überschrieben)
        </span>
        <textarea
          value={userNotes}
          onChange={(e) => setUserNotes(e.target.value)}
          placeholder="Eigene Notizen, Maße, Herkunft, Hinweise…"
          rows={4}
          className="resize-y rounded-md border border-workshop-border bg-workshop-bg p-2 leading-relaxed"
        />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-workshop-muted">
          KI-Beschreibung (vom Inventory Manager gelesen; optional nachbearbeitbar)
        </span>
        <textarea
          value={aiNotes}
          onChange={(e) => setAiNotes(e.target.value)}
          placeholder={
            needsAiScan(item)
              ? "Noch keine echte KI-Beschreibung – „KI beschreiben“ oder Batch-Scan starten."
              : "Wird von der KI erzeugt…"
          }
          className="min-h-[140px] resize-y rounded-md border border-workshop-border bg-workshop-bg p-2 leading-relaxed"
        />
      </label>

      {item.error_message && <p className="text-xs text-workshop-danger">{item.error_message}</p>}
      {showImageKeyHint && (
        <p className="text-[11px] text-workshop-warning">
          Für Bildbeschreibungen fehlt der OpenAI-API-Key unter Einstellungen.
        </p>
      )}
      {showRescanHint && (
        <p className="text-[11px] text-workshop-muted">
          Noch Heuristik-Text – mit „KI beschreiben“ per OpenAI neu scannen (deine Beschreibung bleibt erhalten).
        </p>
      )}
      {process.isError && (
        <p className="text-xs text-workshop-danger">
          {process.error instanceof Error ? process.error.message : "KI-Beschreibung fehlgeschlagen"}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || update.isPending}
          className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
        >
          {update.isPending ? "Speichere…" : "Beschreibungen speichern"}
        </button>
        <button
          type="button"
          onClick={() => process.mutate(item.id)}
          disabled={process.isPending}
          className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-bg disabled:opacity-40"
        >
          {process.isPending ? "KI arbeitet…" : "KI beschreiben"}
        </button>
        {fileUrl && (
          <a
            href={fileUrl}
            download={item.file_name ?? undefined}
            className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-bg"
          >
            Datei öffnen
          </a>
        )}
        {!confirmDelete ? (
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            disabled={remove.isPending}
            className="ml-auto rounded-md border border-workshop-danger/50 px-3 py-1.5 text-xs font-semibold text-workshop-danger hover:bg-workshop-danger/10 disabled:opacity-40"
          >
            Löschen
          </button>
        ) : (
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <span className="text-[11px] text-workshop-warning">Wirklich löschen?</span>
            <button
              type="button"
              onClick={handleDelete}
              disabled={remove.isPending}
              className="rounded-md bg-workshop-danger px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
            >
              {remove.isPending ? "Lösche…" : "Ja, löschen"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmDelete(false)}
              disabled={remove.isPending}
              className="rounded-md border border-workshop-border px-3 py-1.5 text-xs text-workshop-text hover:bg-workshop-bg"
            >
              Abbrechen
            </button>
          </div>
        )}
      </div>

      {update.isSuccess && <p className="text-[11px] text-workshop-success">Gespeichert.</p>}
      {remove.isError && (
        <p className="text-xs text-workshop-danger">
          {remove.error instanceof Error ? remove.error.message : "Löschen fehlgeschlagen"}
        </p>
      )}

      <button
        type="button"
        onClick={() => setShowMeta((v) => !v)}
        className="self-start text-[11px] text-workshop-muted hover:text-workshop-text"
      >
        {showMeta ? "▾" : "▸"} Technische Metadaten
      </button>
      {showMeta && item.vision_result && (
        <pre className="max-h-40 overflow-auto rounded border border-workshop-border bg-black/30 p-2 text-[10px] text-workshop-muted whitespace-pre-wrap">
          {JSON.stringify(item.vision_result, null, 2)}
        </pre>
      )}
      </div>
    </div>
  );
}

function ManualEntryForm({ folderId }: { folderId?: string | null }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [userNotes, setUserNotes] = useState("");
  const create = useCreateManualEntry();

  const handleSubmit = () => {
    if (!title.trim()) return;
    create.mutate(
      {
        title: title.trim(),
        user_notes: userNotes.trim() || undefined,
        folder_id: folderId || undefined,
      },
      {
        onSuccess: () => {
          setTitle("");
          setUserNotes("");
          setOpen(false);
        },
      },
    );
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="self-start rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-bg"
      >
        + Manueller Eintrag
      </button>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border border-workshop-border p-2">
      <input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Titel (z.B. '3x Rest-Multiplex 400x300')"
        autoFocus
        className="rounded-md border border-workshop-border bg-workshop-bg p-1.5 text-xs"
      />
      <textarea
        value={userNotes}
        onChange={(event) => setUserNotes(event.target.value)}
        placeholder="Deine Beschreibung (optional – fließt in die KI-Beschreibung ein)"
        rows={2}
        className="resize-none rounded-md border border-workshop-border bg-workshop-bg p-1.5 text-xs"
      />
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-md border border-workshop-border px-3 py-1.5 text-xs text-workshop-text hover:bg-workshop-bg"
        >
          Abbrechen
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={create.isPending || !title.trim()}
          className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
        >
          Anlegen &amp; beschreiben
        </button>
      </div>
    </div>
  );
}

type FolderFilter = "all" | "unassigned" | string;

function FolderSidebar({
  folders,
  activeFilter,
  onSelect,
}: {
  folders: InventoryFolder[];
  activeFilter: FolderFilter;
  onSelect: (filter: FolderFilter) => void;
}) {
  const createFolder = useCreateInventoryFolder();
  const updateFolder = useUpdateInventoryFolder();
  const deleteFolder = useDeleteInventoryFolder();
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const totalItems = useMemo(() => folders.reduce((sum, f) => sum + f.item_count, 0), [folders]);

  const handleCreate = () => {
    const name = newName.trim();
    if (!name) return;
    createFolder.mutate(
      { name },
      {
        onSuccess: (folder) => {
          setNewName("");
          setCreating(false);
          onSelect(folder.id);
        },
      },
    );
  };

  const handleRename = (id: string) => {
    const name = editName.trim();
    if (!name) return;
    updateFolder.mutate(
      { id, name },
      {
        onSuccess: () => {
          setEditingId(null);
          setEditName("");
        },
      },
    );
  };

  const handleDelete = (id: string) => {
    deleteFolder.mutate(id, {
      onSuccess: () => {
        setConfirmDeleteId(null);
        if (activeFilter === id) onSelect("all");
      },
    });
  };

  const folderBtn = (filter: FolderFilter, label: string, count?: number) => {
    const active = activeFilter === filter;
    return (
      <button
        key={filter}
        type="button"
        onClick={() => onSelect(filter)}
        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition ${
          active
            ? "bg-workshop-accent/15 text-workshop-text"
            : "text-workshop-muted hover:bg-workshop-bg hover:text-workshop-text"
        }`}
      >
        <span className="truncate">{label}</span>
        {count != null && (
          <span className="ml-auto shrink-0 font-mono text-[10px] opacity-70">{count}</span>
        )}
      </button>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
      <div className="shrink-0 text-[10px] font-mono uppercase tracking-wider text-workshop-muted">Ordner</div>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pr-0.5">
        {folderBtn("all", "Alle Einträge")}
        {folderBtn("unassigned", "Ohne Ordner")}
        {folders.map((folder) =>
          editingId === folder.id ? (
            <div key={folder.id} className="flex gap-1 px-1">
              <input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                autoFocus
                className="min-w-0 flex-1 rounded border border-workshop-border bg-workshop-bg px-1.5 py-1 text-xs"
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRename(folder.id);
                  if (e.key === "Escape") setEditingId(null);
                }}
              />
              <button
                type="button"
                onClick={() => handleRename(folder.id)}
                className="rounded border border-workshop-border px-1.5 text-[10px]"
              >
                OK
              </button>
            </div>
          ) : (
            <div key={folder.id} className="group flex items-center gap-0.5">
              <div className="min-w-0 flex-1">{folderBtn(folder.id, folder.name, folder.item_count)}</div>
              <button
                type="button"
                title="Umbenennen"
                onClick={() => {
                  setEditingId(folder.id);
                  setEditName(folder.name);
                }}
                className="shrink-0 rounded px-1 text-[10px] text-workshop-muted opacity-0 hover:text-workshop-text group-hover:opacity-100"
              >
                ✎
              </button>
              {confirmDeleteId === folder.id ? (
                <div className="flex shrink-0 gap-0.5">
                  <button
                    type="button"
                    onClick={() => handleDelete(folder.id)}
                    className="rounded px-1 text-[10px] text-workshop-danger"
                  >
                    Ja
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmDeleteId(null)}
                    className="rounded px-1 text-[10px] text-workshop-muted"
                  >
                    Nein
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  title="Ordner löschen (Einträge bleiben ohne Ordner)"
                  onClick={() => setConfirmDeleteId(folder.id)}
                  className="shrink-0 rounded px-1 text-[10px] text-workshop-muted opacity-0 hover:text-workshop-danger group-hover:opacity-100"
                >
                  ×
                </button>
              )}
            </div>
          ),
        )}
      </div>
      {creating ? (
        <div className="shrink-0 flex gap-1">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Ordnername…"
            autoFocus
            className="min-w-0 flex-1 rounded border border-workshop-border bg-workshop-bg px-2 py-1 text-xs"
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreate();
              if (e.key === "Escape") setCreating(false);
            }}
          />
          <button
            type="button"
            onClick={handleCreate}
            disabled={createFolder.isPending || !newName.trim()}
            className="rounded bg-workshop-accent px-2 py-1 text-[10px] font-semibold text-workshop-bg disabled:opacity-40"
          >
            OK
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="shrink-0 rounded-md border border-workshop-border px-2 py-1.5 text-xs font-semibold text-workshop-text hover:bg-workshop-bg"
        >
          + Ordner
        </button>
      )}
      {createFolder.isError && (
        <p className="text-[10px] text-workshop-danger">
          {createFolder.error instanceof Error ? createFolder.error.message : "Ordner konnte nicht angelegt werden"}
        </p>
      )}
      <p className="shrink-0 text-[10px] text-workshop-muted">{totalItems} in Ordnern</p>
    </div>
  );
}

/** Inventar-Bibliothek: Dateien aller Formate + editierbare KI-Beschreibung. */
export function AssetLibrary({
  listHeader,
}: {
  listHeader?: ReactNode;
} = {}) {
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [folderFilter, setFolderFilter] = useState<FolderFilter>("all");
  const { data: folders = [] } = useInventoryFolders();
  const itemFilters = useMemo(() => {
    const base: { search?: string; folder_id?: string | null } = { search: search || undefined };
    if (folderFilter === "unassigned") base.folder_id = null;
    else if (folderFilter !== "all") base.folder_id = folderFilter;
    return base;
  }, [search, folderFilter]);
  const { data, isLoading } = useInventoryItems(itemFilters);
  const processPending = useProcessPendingItems();
  const trainKg = useTrainKnowledgeGraph();
  const { data: kgStats } = useKnowledgeGraphStats();

  const uploadFolderId = folderFilter !== "all" && folderFilter !== "unassigned" ? folderFilter : null;

  const selected = useMemo(
    () => data?.items.find((i) => i.id === selectedId) ?? null,
    [data?.items, selectedId],
  );

  const scanCount = useMemo(() => (data?.items ?? []).filter(needsAiScan).length, [data?.items]);

  useEffect(() => {
    if (selectedId && data && !data.items.some((i) => i.id === selectedId)) {
      setSelectedId(null);
    }
  }, [data, selectedId]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-workshop-muted">
          {isLoading
            ? "Lädt…"
            : `${data?.total ?? 0} Dateien & Einträge` +
              (scanCount > 0 ? ` · ${scanCount} ohne echte KI-Beschreibung` : "") +
              (kgStats
                ? ` · KG: ${kgStats.nodes} Nodes / ${kgStats.learned_edges} gelernt`
                : "")}
        </span>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => trainKg.mutate()}
            disabled={trainKg.isPending}
            title="Inventory Knowledge Graph aus aktuellen DB-Einträgen anlernen"
            className="rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:border-workshop-accent disabled:opacity-40"
          >
            {trainKg.isPending ? "KG lernt…" : "Knowledge Graph anlernen"}
          </button>
          <button
            type="button"
            onClick={() => processPending.mutate(50)}
            disabled={processPending.isPending || scanCount === 0}
            title="Neue und stub-/heuristik-beschriebene Einträge per KI scannen"
            className="rounded-md bg-workshop-accent px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
          >
            {processPending.isPending ? "KI scannt…" : "Scanne alle neuen DB Einträge mit KI"}
          </button>
        </div>
      </div>

      {trainKg.isSuccess && (
        <p className="text-xs text-workshop-success">
          Knowledge Graph aktualisiert: {trainKg.data.nodes} Nodes, {trainKg.data.edges} Kanten
          {trainKg.data.learned_edges != null ? ` (${trainKg.data.learned_edges} gelernt)` : ""}.
        </p>
      )}
      {trainKg.isError && (
        <p className="text-xs text-workshop-danger">
          KG-Training fehlgeschlagen:{" "}
          {trainKg.error instanceof Error ? trainKg.error.message : "Unbekannter Fehler"}
        </p>
      )}

      {processPending.isSuccess && (
        <p className="text-xs text-workshop-success">
          {processPending.data.processed} gescannt – {processPending.data.indexed} beschrieben,{" "}
          {processPending.data.failed} fehlgeschlagen.
        </p>
      )}
      {processPending.isError && (
        <p className="text-xs text-workshop-danger">
          Scan fehlgeschlagen:{" "}
          {processPending.error instanceof Error ? processPending.error.message : "Unbekannter Fehler"}
        </p>
      )}

      <input
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="Suche in Titel, Beschreibung oder Dateiname…"
        className="shrink-0 rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs"
      />

      <div className="flex min-h-0 flex-1 gap-3 overflow-hidden">
        <aside className="flex w-52 shrink-0 flex-col gap-3 overflow-hidden rounded-md border border-workshop-border bg-workshop-bg/30 p-2 lg:w-56">
          <div className="shrink-0">
            <UploadDropzone folderId={uploadFolderId} />
          </div>
          <FolderSidebar folders={folders} activeFilter={folderFilter} onSelect={setFolderFilter} />
          <div className="shrink-0 border-t border-workshop-border pt-2">
            <CrawlerPanel />
          </div>
        </aside>

        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2 overflow-hidden lg:flex-row">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2 overflow-hidden">
            <div className="shrink-0">
              <ManualEntryForm folderId={uploadFolderId} />
            </div>
            {listHeader && <div className="shrink-0">{listHeader}</div>}
            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-0.5">
              {data?.items.length === 0 && (
                <p className="text-xs text-workshop-muted">Keine Einträge in dieser Ansicht.</p>
              )}
              {data?.items.map((item) => {
                const active = item.id === selectedId;
                const preview = previewDescription(item);
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setSelectedId(item.id)}
                    className={`w-full rounded-md border p-2 text-left text-xs transition ${
                      active
                        ? "border-workshop-accent bg-workshop-accent/10"
                        : "border-workshop-border hover:border-workshop-muted"
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      {item.file_path ? (
                        <AssetPreview
                          itemId={item.id}
                          fileType={item.file_type}
                          fileName={item.file_name ?? item.title}
                          compact
                        />
                      ) : (
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded border border-workshop-border text-[9px] text-workshop-muted">
                          —
                        </div>
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-semibold text-workshop-text">
                          {item.title ?? item.file_name ?? "Unbenannt"}
                        </div>
                        <div className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] text-workshop-muted">
                          <span>{item.file_type}</span>
                          <span className={STATUS_COLORS[item.status]}>{item.status}</span>
                          {categoryOf(item) && <span>{categoryOf(item)}</span>}
                          {item.tags?.includes("KI-Generiert") && (
                            <span className="text-workshop-accent">KI-Generiert</span>
                          )}
                          {item.tags?.includes("generated_3d") && (
                            <span className="text-workshop-accent">3D-Modell</span>
                          )}
                          {needsAiScan(item) && <span className="text-workshop-warning">KI-Scan nötig</span>}
                        </div>
                        <p className="mt-1 line-clamp-2 text-[11px] text-workshop-muted">
                          {preview || "Noch keine Beschreibung"}
                        </p>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex min-h-[12rem] w-full shrink-0 flex-col overflow-hidden rounded-md border border-workshop-border bg-workshop-bg/40 p-3 lg:min-h-0 lg:w-80 xl:w-96">
            {selected ? (
              <AssetDetail item={selected} folders={folders} onClose={() => setSelectedId(null)} />
            ) : (
              <p className="text-xs text-workshop-muted">
                Wähle einen Eintrag für Voransicht und Beschreibung. Upload und Ordner sind links fixiert – du musst
                nicht mehr durch die Liste scrollen. „Scanne alle neuen DB Einträge mit KI“ analysiert neue und
                stub-beschriebene Dateien.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
