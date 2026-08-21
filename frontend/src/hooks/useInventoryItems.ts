import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type {
  ConceptToInventoryRequest,
  ConceptToInventoryResponse,
  InventoryFolder,
  InventoryFolderCreateRequest,
  InventoryFolderUpdateRequest,
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
  /** null = ohne Ordner; undefined = alle Ordner */
  folder_id?: string | null;
}

const QUERY_KEY = "inventory-items";
const FOLDERS_QUERY_KEY = "inventory-folders";

function buildQuery(filters: InventoryItemFilters): string {
  const params = new URLSearchParams();
  if (filters.search) params.set("search", filters.search);
  if (filters.status) params.set("status", filters.status);
  if (filters.file_type) params.set("file_type", filters.file_type);
  if (filters.source) params.set("source", filters.source);
  if (filters.folder_id === null) params.set("folder_id", "null");
  else if (filters.folder_id) params.set("folder_id", filters.folder_id);
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
    refetchInterval: false as const,
  });
}

export function useCreateManualEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ManualEntryCreateRequest) => api.post<InventoryItem>("/api/v1/inventory/items", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
      queryClient.invalidateQueries({ queryKey: [FOLDERS_QUERY_KEY] });
    },
  });
}

export function useUpdateInventoryItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: InventoryItemUpdateRequest & { id: string }) =>
      api.patch<InventoryItem>(`/api/v1/inventory/items/${id}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
      queryClient.invalidateQueries({ queryKey: [FOLDERS_QUERY_KEY] });
    },
  });
}

export function useDeleteInventoryItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<{ status: string; id: string }>(`/api/v1/inventory/items/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
      queryClient.invalidateQueries({ queryKey: [FOLDERS_QUERY_KEY] });
    },
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

export interface KnowledgeGraphStats {
  nodes: number;
  edges: number;
  learned_edges: number;
  by_type: Record<string, number>;
  trained_at?: string | null;
  updated_at?: string | null;
  path?: string;
}

export function useKnowledgeGraphStats() {
  return useQuery({
    queryKey: ["inventory-knowledge-graph"],
    queryFn: () => api.get<KnowledgeGraphStats>("/api/v1/inventory/knowledge-graph"),
    refetchInterval: false as const,
  });
}

/** Baut den Inventory Knowledge Graph aus aktuellen DB-Einträgen neu auf. */
export function useTrainKnowledgeGraph() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<KnowledgeGraphStats>("/api/v1/inventory/knowledge-graph/train", {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["inventory-knowledge-graph"] }),
  });
}

export function useInventoryFolders() {
  return useQuery({
    queryKey: [FOLDERS_QUERY_KEY],
    queryFn: () => api.get<InventoryFolder[]>("/api/v1/inventory/folders"),
    refetchInterval: false as const,
  });
}

export function useCreateInventoryFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: InventoryFolderCreateRequest) =>
      api.post<InventoryFolder>("/api/v1/inventory/folders", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [FOLDERS_QUERY_KEY] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
    },
  });
}

export function useUpdateInventoryFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: InventoryFolderUpdateRequest & { id: string }) =>
      api.patch<InventoryFolder>(`/api/v1/inventory/folders/${id}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [FOLDERS_QUERY_KEY] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
    },
  });
}

export function useDeleteInventoryFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<{ status: string; id: string }>(`/api/v1/inventory/folders/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [FOLDERS_QUERY_KEY] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY] });
    },
  });
}
