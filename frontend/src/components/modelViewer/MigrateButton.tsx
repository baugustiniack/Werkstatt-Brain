import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { CadMigrateResponse } from "../../api/types";

interface MigrateButtonProps {
  sessionId: string | null;
  canMigrate: boolean;
  partIndex?: number;
}

/** 1-Click-Migration in Inventar-DB & Qdrant (SPEC Kap. 2.3.3, 5.2 Panel 3). */
export function MigrateButton({ sessionId, canMigrate, partIndex }: MigrateButtonProps) {
  const [projectName, setProjectName] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      api.post<CadMigrateResponse>("/api/v1/inventory/migrate-cad", {
        session_id: sessionId,
        project_name: projectName || undefined,
        part_index: partIndex,
      }),
  });

  if (!canMigrate) return null;

  return (
    <div className="flex flex-col gap-2 rounded-md border border-workshop-border p-3">
      <input
        type="text"
        value={projectName}
        onChange={(event) => setProjectName(event.target.value)}
        placeholder="Projektname (optional)"
        className="rounded-md border border-workshop-border bg-workshop-bg p-2 text-xs"
      />
      <button
        type="button"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        className="rounded-md bg-workshop-success px-3 py-1.5 text-xs font-semibold text-workshop-bg disabled:opacity-40"
      >
        💾 {mutation.isPending ? "Migriere…" : "In Inventar-DB speichern"}
      </button>
      {mutation.isSuccess && (
        <p className="text-xs text-workshop-success">
          Gespeichert als „{mutation.data.project_name}“ (Qdrant: {mutation.data.qdrant_indexed ? "indiziert" : "n/a"}).
        </p>
      )}
      {mutation.isError && <p className="text-xs text-workshop-danger">{(mutation.error as Error).message}</p>}
    </div>
  );
}
