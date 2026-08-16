import { useEffect, useMemo, useState } from "react";

import type { InventoryItem } from "../../api/types";
import {
  useCreateManualEntry,
  useDeleteInventoryItem,
  useInventoryItems,
  useKnowledgeGraphStats,
  useProcessInventoryItem,
  useProcessPendingItems,
  useTrainKnowledgeGraph,
  useUpdateInventoryItem,
} from "../../hooks/useInventoryItems";
import { useApiKeyStatus } from "../../hooks/useSettings";
import { AssetPreview, assetFileUrl } from "./AssetPreview";

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
  onClose,
}: {
  item: InventoryItem;
  onClose: () => void;
}) {
  const update = useUpdateInventoryItem();
  const process = useProcessInventoryItem();
  const remove = useDeleteInventoryItem();
  const { data: keyStatus } = useApiKeyStatus();
  const [title, setTitle] = useState(item.title ?? "");
  const [userNotes, setUserNotes] = useState(userNotesOf(item));
  const [aiNotes, setAiNotes] = useState(aiNotesOf(item));
  const [showMeta, setShowMeta] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileUrl = item.file_path ? assetFileUrl(item.id) : null;
  const dirty =
    title.trim() !== (item.title ?? "").trim() ||
    userNotes.trim() !== userNotesOf(item) ||
    aiNotes.trim() !== aiNotesOf(item);
  const openaiReady = !!keyStatus?.openai_configured;
  const showImageKeyHint = item.file_type === "image" && !openaiReady;
  const showRescanHint = item.file_type === "image" && openaiReady && needsAiScan(item);

  useEffect(() => {
    setTitle(item.title ?? "");
    setUserNotes(userNotesOf(item));
    setAiNotes(aiNotesOf(item));
    setShowMeta(false);
    setConfirmDelete(false);
  }, [item.id, item.title, item.user_notes, item.ai_notes, item.notes, item.vision_result]);

  const handleSave = () => {
    update.mutate({
      id: item.id,
      title: title.trim() || undefined,
      user_notes: userNotes,
      ai_notes: aiNotes,
    });
  };

  const handleDelete = () => {
    remove.mutate(item.id, {
      onSuccess: () => onClose(),
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
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

      <label className="flex min-h-0 flex-1 flex-col gap-1 text-xs">
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
          className="min-h-[140px] flex-1 resize-y rounded-md border border-workshop-border bg-workshop-bg p-2 leading-relaxed"
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
  );
}

function ManualEntryForm() {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [userNotes, setUserNotes] = useState("");
  const create = useCreateManualEntry();

  const handleSubmit = () => {
    if (!title.trim()) return;
    create.mutate(
      { title: title.trim(), user_notes: userNotes.trim() || undefined },
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

/** Inventar-Bibliothek: Dateien aller Formate + editierbare KI-Beschreibung. */
export function AssetLibrary() {
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data, isLoading } = useInventoryItems({ search: search || undefined });
  const processPending = useProcessPendingItems();
  const trainKg = useTrainKnowledgeGraph();
  const { data: kgStats } = useKnowledgeGraphStats();

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
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
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
        className="rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs"
      />

      <ManualEntryForm />

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="flex max-h-[28rem] flex-col gap-2 overflow-y-auto lg:max-h-none">
          {data?.items.length === 0 && <p className="text-xs text-workshop-muted">Keine Einträge gefunden.</p>}
          {data?.items.map((item) => {
            const active = item.id === selectedId;
            const preview = previewDescription(item);
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setSelectedId(item.id)}
                className={`rounded-md border p-2 text-left text-xs transition ${
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

        <div className="min-h-[16rem] rounded-md border border-workshop-border bg-workshop-bg/40 p-3">
          {selected ? (
            <AssetDetail item={selected} onClose={() => setSelectedId(null)} />
          ) : (
            <p className="text-xs text-workshop-muted">
              Wähle links einen Eintrag für Voransicht und Beschreibung. „Scanne alle neuen DB Einträge mit KI“
              analysiert neue und stub-beschriebene Dateien. Bilder: OpenAI-Key. PDF/STL/Text: Anthropic oder Cursor.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
