import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type {
  ConceptToInventoryRequest,
  ConceptToInventoryResponse,
  InventoryItem,
  InventoryItemListResponse,
  InventoryItemUpdateRequest,
  ManualEntryCreateRequest,
  ProcessPendingResponse,
} from "../api/types";

export interface InventoryItemFilters {
  search?: string;
  status?: string;
  file_type?: string;
  source?: string;
}

const QUERY_KEY = "inventory-items";

function buildQuery(filters: InventoryItemFilters): string {
  const params = new URLSearchParams();
  if (filters.search) params.set("search", filters.search);
  if (filters.status) params.set("status", filters.status);
  if (filters.file_type) params.set("file_type", filters.file_type);
  if (filters.source) params.set("source", filters.source);
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** Unstrukturierte Asset-Bibliothek (Nutzer-Feedback: einfache, durchsuchbare
 * Ablage für Dateien + manuelle Einträge, statt starrer Fräser-/Materialien-
 * Tabellen; KI-Strukturierung erfolgt nachträglich, auf Wunsch/im Hintergrund). */
export function useInventoryItems(filters: InventoryItemFilters) {
  return useQuery({
    queryKey: [QUERY_KEY, filters],
    queryFn: () => api.get<InventoryItemListResponse>(`/api/v1/inventory/items${buildQuery(filters)}`),
    refetchInterval: 20_000,
  });
}

export function useCreateManualEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ManualEntryCreateRequest) => api.post<InventoryItem>("/api/v1/inventory/items", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [QUERY_KEY] }),
  });
}

export function useUpdateInventoryItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: InventoryItemUpdateRequest & { id: string }) =>
      api.patch<InventoryItem>(`/api/v1/inventory/items/${id}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [QUERY_KEY] }),
  });
}

export function useProcessInventoryItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<InventoryItem>(`/api/v1/inventory/items/${id}/process`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [QUERY_KEY] }),
  });
}

export function useProcessPendingItems() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (limit: number) =>
      api.post<ProcessPendingResponse>(
        `/api/v1/inventory/process-pending?limit=${limit}&include_stubs=true`,
        {},
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [QUERY_KEY] }),
  });
}

/** Speichert ein generiertes Konzept-Foto in der Inventar-DB (mit Chat-Link). */
export function useSaveConceptToInventory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConceptToInventoryRequest) =>
      api.post<ConceptToInventoryResponse>("/api/v1/inventory/from-concept", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}
