import { apiBaseUrl } from "../../api/client";

interface DownloadCenterProps {
  sessionId: string | null;
  /** Index innerhalb `completed_parts` (Mehrteil-Ausarbeitung); ohne Angabe
   * liefert das Backend das zuletzt abgeschlossene Teil. */
  partIndex?: number;
  /** Persistierte Artefakt-URLs (Conversation), wenn keine Live-Session. */
  stepUrl?: string | null;
  stlUrl?: string | null;
}

/** Download-Buttons für STEP/STL (und deaktiviert NC/G-Code, SPEC Kap. 4.1, 5.2 Panel 3). */
export function DownloadCenter({ sessionId, partIndex, stepUrl, stlUrl }: DownloadCenterProps) {
  const query = partIndex !== undefined ? `?part_index=${partIndex}` : "";
  const liveStep = sessionId ? `${apiBaseUrl()}/api/v1/cad/download/${sessionId}/step${query}` : null;
  const liveStl = sessionId ? `${apiBaseUrl()}/api/v1/cad/download/${sessionId}/stl${query}` : null;
  const resolvedStep = liveStep ?? stepUrl ?? null;
  const resolvedStl = liveStl ?? stlUrl ?? null;

  return (
    <div className="flex flex-wrap gap-2">
      <a
        href={resolvedStep ?? undefined}
        aria-disabled={!resolvedStep}
        className={`rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold ${
          !resolvedStep ? "pointer-events-none opacity-40" : "text-workshop-text hover:bg-workshop-bg"
        }`}
      >
        ⬇ .STEP
      </a>
      <a
        href={resolvedStl ?? undefined}
        aria-disabled={!resolvedStl}
        className={`rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold ${
          !resolvedStl ? "pointer-events-none opacity-40" : "text-workshop-text hover:bg-workshop-bg"
        }`}
      >
        ⬇ .STL
      </a>
      <span
        title="G-Code-/CAM-Postprozessor ist noch nicht implementiert (zukünftiges Kapitel)."
        className="cursor-not-allowed rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-muted opacity-40"
      >
        ⬇ .NC
      </span>
    </div>
  );
}
