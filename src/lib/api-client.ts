// ── Unified API Client ─────────────────────────────────
// Single HTTP client for all API calls.
// Handles: SSR vs client routing, params, retries, error formatting.

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

const SSR_BASE = "http://127.0.0.1:8100";
const isServer = typeof window === "undefined";

/**
 * Build the correct fetch URL:
 * - Server (SSR): absolute URL to backend (http://127.0.0.1:8100/api/v1/...)
 * - Client (browser): relative path (/api/v1/...) → nginx proxies to backend
 */
function buildUrl(path: string, params?: Record<string, string | number | undefined | null>): string {
  // Use absolute base for URL construction (needed for search params)
  const base = API_URL.startsWith("http") ? API_URL : SSR_BASE;
  const url = new URL(`${base}${path}`);

  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
  }

  // Client: relative path through nginx proxy; Server: absolute to backend
  if (!isServer && !API_URL.startsWith("http")) {
    return `${path}${url.search}`;
  }
  return url.toString();
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public url: string,
  ) {
    super(`API ${status}: ${statusText} (${url})`);
    this.name = "ApiError";
  }
}

/**
 * Core fetch with retry on 5xx / network errors.
 */
async function fetchWithRetry(url: string, init?: RequestInit, retries = 2): Promise<Response> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(url, init);
      if (res.ok || res.status < 500) return res;
      lastError = new ApiError(res.status, res.statusText, url);
    } catch (e) {
      lastError = e instanceof Error ? e : new Error(String(e));
    }

    if (attempt < retries) {
      await new Promise((r) => setTimeout(r, 300 * (attempt + 1)));
    }
  }

  throw lastError;
}

// ── Public API ─────────────────────────────────────────

interface FetchOptions {
  /** Cache revalidation in seconds (default 300 for GET, 0 for mutations) */
  revalidate?: number;
  /** HTTP method */
  method?: string;
  /** JSON body */
  body?: unknown;
  /** Extra headers */
  headers?: Record<string, string>;
  /** Number of retries on 5xx (default 2) */
  retries?: number;
}

/**
 * Typed API fetch — single entry point for all API calls.
 *
 * @example
 * const data = await api<CountriesResponse>("/api/v1/countries", { days: 30 });
 * const thread = await api<ThreadDetail>(`/api/v1/threads/${id}`);
 */
export async function api<T>(
  path: string,
  params?: Record<string, string | number | undefined | null>,
  options: FetchOptions = {},
): Promise<T> {
  const { method = "GET", body, headers, revalidate = 300, retries = 2 } = options;

  const url = buildUrl(path, params);

  const init: RequestInit = {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    ...(method === "GET" ? { next: { revalidate } } : {}),
  };

  const res = await fetchWithRetry(url, init, retries);

  if (!res.ok) {
    const err = new ApiError(res.status, res.statusText, url);
    // Show toast on client side
    if (typeof window !== "undefined") {
      import("@/components/ApiErrorToast").then(({ showApiError }) => {
        showApiError(`API ${res.status}: ${path}`, res.status >= 500 ? "error" : "warning");
      }).catch(() => {});
    }
    throw err;
  }

  return res.json();
}

/**
 * Fire-and-forget mutation (POST/PUT/DELETE).
 */
export async function apiMutate<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  return api<T>(path, undefined, { ...options, method: options.method ?? "POST", revalidate: 0 });
}

// Re-export for backward compat
export { API_URL };
