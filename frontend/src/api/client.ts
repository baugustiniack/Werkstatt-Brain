const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL ?? "ws://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text().catch(() => undefined);
    }
    const message =
      (typeof detail === "object" && detail !== null && "detail" in detail
        ? String((detail as { detail: unknown }).detail)
        : undefined) ?? `Request fehlgeschlagen: ${response.status} ${response.statusText}`;
    throw new ApiError(message, response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, init?: RequestInit) => request<T>(path, init),
  post: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined, ...init }),
  patch: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, { method: "PATCH", body: body !== undefined ? JSON.stringify(body) : undefined, ...init }),
  delete: <T>(path: string, init?: RequestInit) => request<T>(path, { method: "DELETE", ...init }),
  postForm: async <T>(path: string, form: FormData, init?: RequestInit): Promise<T> => {
    const response = await fetch(`${API_BASE_URL}${path}`, { method: "POST", body: form, ...init });
    if (!response.ok) {
      if (init?.signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const detail = await response.json().catch(() => undefined);
      const message =
        (detail && typeof detail === "object" && "detail" in detail ? String(detail.detail) : undefined) ??
        `Upload fehlgeschlagen: ${response.status}`;
      throw new ApiError(message, response.status, detail);
    }
    return (await response.json()) as T;
  },
};

export function apiBaseUrl(): string {
  return API_BASE_URL;
}

export function wsUrl(path: string): string {
  return `${WS_BASE_URL}${path}`;
}
