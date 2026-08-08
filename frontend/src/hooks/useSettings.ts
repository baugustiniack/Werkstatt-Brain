import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ApiKeyStatusResponse, LlmProviderChoice } from "../api/types";

/** UI-verwaltete API-Key-Einstellungen (Nutzer-Feedback: Key flexibel per
 * Eingabefeld statt nur über .env pflegen können). */
export function useApiKeyStatus() {
  return useQuery({
    queryKey: ["api-key-status"],
    queryFn: () => api.get<ApiKeyStatusResponse>("/api/v1/settings/api-keys"),
  });
}

export function useUpdateApiKeys() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      anthropic_api_key?: string | null;
      openai_api_key?: string | null;
      cursor_api_key?: string | null;
      llm_provider?: LlmProviderChoice;
    }) => api.post<ApiKeyStatusResponse>("/api/v1/settings/api-keys", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-key-status"] }),
  });
}
