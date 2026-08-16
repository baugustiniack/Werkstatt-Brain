import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationMessage {
  id: string;
  role: string;
  content: string;
  cad_session_id: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
}

export interface ConversationArtifact {
  id: string;
  kind: string;
  label: string | null;
  part_index: number | null;
  cad_session_id: string | null;
  url: string;
  meta?: Record<string, unknown> | null;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ConversationMessage[];
  artifacts: ConversationArtifact[];
}

const KEY = "conversations";

export function useConversations() {
  return useQuery({
    queryKey: [KEY],
    queryFn: () => api.get<ConversationSummary[]>("/api/v1/conversations"),
  });
}

export function useConversation(id: string | null) {
  return useQuery({
    queryKey: [KEY, id],
    queryFn: () => api.get<ConversationDetail>(`/api/v1/conversations/${id}`),
    enabled: !!id,
    // Kein Cross-Chat-Placeholder: sonst wirkt es, als würden Nachrichten „verschwinden“
    staleTime: 5_000,
  });
}

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (title?: string) =>
      api.post<ConversationSummary>("/api/v1/conversations", title ? { title } : {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: [KEY] }),
  });
}

export function useAddConversationMessage(conversationId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      role: string;
      content: string;
      cad_session_id?: string | null;
      meta?: Record<string, unknown>;
    }) => api.post<ConversationMessage>(`/api/v1/conversations/${conversationId}/messages`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [KEY] });
      if (conversationId) qc.invalidateQueries({ queryKey: [KEY, conversationId] });
    },
  });
}

export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<{ status: string; id: string }>(`/api/v1/conversations/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: [KEY] }),
  });
}

export function invalidateConversation(qc: ReturnType<typeof useQueryClient>, id: string | null) {
  qc.invalidateQueries({ queryKey: [KEY] });
  if (id) qc.invalidateQueries({ queryKey: [KEY, id] });
}
