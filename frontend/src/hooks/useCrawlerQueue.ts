import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { CrawlerQueueResponse, CrawlerScanResponse } from "../api/types";

export function useCrawlerQueue() {
  return useQuery({
    queryKey: ["crawler-queue"],
    queryFn: () => api.get<CrawlerQueueResponse>("/api/v1/crawler/queue"),
    refetchInterval: 15_000,
  });
}

export function useTriggerCrawlerScan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<CrawlerScanResponse>("/api/v1/crawler/scan", {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["crawler-queue"] }),
  });
}
