const ENV_API = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const ENV_WS = import.meta.env.VITE_WS_BASE_URL ?? "ws://localhost:8000";

function isLoopback(host: string): boolean {
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

/**
 * Im Vite-Dev (auch per LAN-IP vom Handy) same-origin nutzen.
 * Der Dev-Server proxyt /api und /health zum Backend – Handy braucht kein Port 8000.
 */
function resolveApiBaseUrl(): string {
  if (typeof window === "undefined") return ENV_API;
  if (import.meta.env.DEV) {
    return "";
  }
  try {
    const pageHost = window.location.hostname;
    if (!pageHost || isLoopback(pageHost)) return ENV_API;
    const configured = new URL(ENV_API, window.location.origin);
    if (isLoopback(configured.hostname)) {
      return `${window.location.protocol}//${pageHost}:8000`;
    }
  } catch {
    /* ignore */
  }
  return ENV_API;
}

function resolveWsBaseUrl(): string {
  if (typeof window === "undefined") return ENV_WS;
  if (import.meta.env.DEV) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}`;
  }
  try {
    const pageHost = window.location.hostname;
    if (!pageHost || isLoopback(pageHost)) return ENV_WS;
    const configured = new URL(ENV_WS.replace(/^ws/, "http"), window.location.origin);
    if (isLoopback(configured.hostname)) {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      return `${proto}//${pageHost}:8000`;
    }
  } catch {
    /* ignore */
  }
  return ENV_WS;
}

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
  const response = await fetch(`${resolveApiBaseUrl()}${path}`, {
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
    const response = await fetch(`${resolveApiBaseUrl()}${path}`, { method: "POST", body: form, ...init });
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
  return resolveApiBaseUrl();
}

export function wsUrl(path: string): string {
  return `${resolveWsBaseUrl()}${path}`;
}
