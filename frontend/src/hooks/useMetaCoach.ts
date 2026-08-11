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
  enabled_agents?: string[];
  fixed_agents?: string[];
  active_config?: {
    id?: string;
    name?: string;
    is_standard?: boolean;
    description?: string | null;
  } | null;
  profiles_updated_at?: string | null;
}

export interface AgentWorkflowConfigRow {
  id: string;
  name: string;
  description?: string | null;
  is_standard: boolean;
  is_active: boolean;
  agents: Record<string, AgentProfile>;
  edges: WorkflowEdge[];
  config: WorkflowConfig;
  enabled_agents: string[];
  fixed_agents: string[];
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MetaCoachChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface MetaCoachRecommendation {
  type?: string;
  agent?: string | null;
  title?: string;
  detail?: string;
  priority?: string;
}

export interface MetaCoachChatResponse {
  reply: string;
  actions: Record<string, unknown>[];
  recommendations?: MetaCoachRecommendation[];
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
const WF_KEY = "agent-workflows";

export function useWorkflowOverview() {
  return useQuery({
    queryKey: [KEY, "workflow"],
    queryFn: () => api.get<WorkflowOverview>("/api/v1/meta-coach/workflow"),
  });
}

export function useAgentWorkflowConfigs() {
  return useQuery({
    queryKey: [WF_KEY],
    queryFn: () => api.get<AgentWorkflowConfigRow[]>("/api/v1/agent-workflows"),
  });
}

export function useActivateWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<AgentWorkflowConfigRow>(`/api/v1/agent-workflows/${id}/activate`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [WF_KEY] });
      void qc.invalidateQueries({ queryKey: [KEY, "workflow"] });
    },
  });
}

export function useActivateStandardWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AgentWorkflowConfigRow>("/api/v1/agent-workflows/standard/activate", {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [WF_KEY] });
      void qc.invalidateQueries({ queryKey: [KEY, "workflow"] });
    },
  });
}

export function useSaveWorkflowConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      description?: string;
      agents?: Record<string, AgentProfile>;
      edges?: WorkflowEdge[];
      enabled_agents?: string[];
      config?: Partial<WorkflowConfig>;
      activate?: boolean;
    }) => api.post<AgentWorkflowConfigRow>("/api/v1/agent-workflows", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [WF_KEY] });
      void qc.invalidateQueries({ queryKey: [KEY, "workflow"] });
    },
  });
}

export function useUpdateActiveWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      enabled_agents?: string[];
      config?: Partial<WorkflowConfig>;
      description?: string;
      name?: string;
    }) => api.patch<AgentWorkflowConfigRow>("/api/v1/agent-workflows/active", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [WF_KEY] });
      void qc.invalidateQueries({ queryKey: [KEY, "workflow"] });
    },
  });
}

export function useUpdateActiveAgentProperty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      agentId,
      property,
      value,
    }: {
      agentId: string;
      property: string;
      value: unknown;
    }) =>
      api.patch<{ agent_id: string; agent: AgentProfile }>(
        `/api/v1/agent-workflows/active/agents/${agentId}`,
        { property, value },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [WF_KEY] });
      void qc.invalidateQueries({ queryKey: [KEY, "workflow"] });
    },
  });
}

export function useDeleteWorkflowConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<{ status: string }>(`/api/v1/agent-workflows/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [WF_KEY] });
      void qc.invalidateQueries({ queryKey: [KEY, "workflow"] });
    },
  });
}

export function useMetaCoachLogs(limit = 80) {
  return useQuery({
    queryKey: [KEY, "logs", limit],
    queryFn: () => api.get<MetaCoachLogSummary[]>(`/api/v1/meta-coach/logs?limit=${limit}`),
  });
}

export function useMetaCoachChat() {
  return useMutation({
    mutationFn: (body: {
      messages: MetaCoachChatMessage[];
      log_names?: string[];
    }) =>
      api.post<MetaCoachChatResponse>("/api/v1/meta-coach/chat", {
        ...body,
        apply_actions: false,
      }),
  });
}
