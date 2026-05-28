/**
 * Thin wrapper over fetch() with three responsibilities:
 *   1. Prepend /api so calls read like `api.get("/diagnostics/summary")`.
 *   2. Attach the bearer token (Phase 21) from localStorage on every call.
 *   3. Throw on non-2xx so TanStack Query handles errors uniformly.
 *      On 401 it also clears the stored token and redirects to /login,
 *      so an expired/invalid session bounces the user to sign in again.
 *
 * The Vite dev server proxies /api/* to http://localhost:8000 (see vite.config.ts).
 * In production, the same path will be served from the FastAPI host.
 */
import { TOKEN_KEY, USER_KEY } from "@/lib/auth";

class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`HTTP ${status}: ${detail}`);
  }
}

function authHeader(): Record<string, string> {
  try {
    const t = window.localStorage.getItem(TOKEN_KEY);
    return t ? { Authorization: `Bearer ${t}` } : {};
  } catch {
    return {};
  }
}

function handleUnauthorized() {
  // Token missing/expired/invalid — clear session and bounce to login.
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
  } catch { /* ignore */ }
  // Avoid redirect loops if we're already on the login page.
  if (window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { ...authHeader() };
  if (body) headers["Content-Type"] = "application/json";

  const res = await fetch(`/api${path}`, {
    method,
    headers: Object.keys(headers).length ? headers : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401) {
    handleUnauthorized();
    throw new ApiError(401, "Not authenticated.");
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      if (j?.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      // not JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  delete: (path: string) => request<void>("DELETE", path),
};

export { ApiError };
