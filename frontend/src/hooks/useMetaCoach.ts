import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";

export interface AgentProfile {
  display_name: string;
  role: string;
  responsibilities?: string[];
  inputs?: string[];
  outputs?: string[];
  properties?: Record<string, unknown>;
}

export interface WorkflowEdge {
  from: string;
  to: string;
  when?: string;
  loop?: boolean;
}

export interface WorkflowConfig {
  max_iterations: number;
  sandbox_timeout_seconds: number;
  description?: string;
  notes?: string;
  updated_at?: string;
}

export interface WorkflowOverview {
  agents: Record<string, AgentProfile>;
  edges: WorkflowEdge[];
  config: WorkflowConfig;
  profiles_updated_at?: string | null;
}

export interface MetaCoachChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface MetaCoachChatResponse {
  reply: string;
  actions: Record<string, unknown>[];
  applied_changes: Record<string, unknown>[];
  referenced_logs: unknown[];
  llm_configured: boolean;
  workflow?: WorkflowOverview | null;
}

export interface MetaCoachLogSummary {
  name: string;
  path: string;
  processed: boolean;
  size_bytes: number;
  modified_at: string;
}

const KEY = "meta-coach";

export function useWorkflowOverview() {
  return useQuery({
    queryKey: [KEY, "workflow"],
    queryFn: () => api.get<WorkflowOverview>("/api/v1/meta-coach/workflow"),
  });
}

export function useMetaCoachLogs(limit = 80) {
  return useQuery({
    queryKey: [KEY, "logs", limit],
    queryFn: () => api.get<MetaCoachLogSummary[]>(`/api/v1/meta-coach/logs?limit=${limit}`),
  });
}

export function useMetaCoachChat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      messages: MetaCoachChatMessage[];
      log_names?: string[];
      apply_actions?: boolean;
    }) => api.post<MetaCoachChatResponse>("/api/v1/meta-coach/chat", body),
    onSuccess: (data) => {
      if (data.applied_changes?.length) {
        qc.invalidateQueries({ queryKey: [KEY, "workflow"] });
      }
    },
  });
}
