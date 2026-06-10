/**
 * authHeaders — bearer-token header helper.
 *
 * A few calc components still use raw fetch() for writes instead of the
 * shared api client. The RBAC middleware requires a token on every /api
 * route, so those writes 401 ("Not authenticated."). Wrap their headers
 * with this so the stored token is attached.
 *
 *   fetch(url, { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body })
 *   fetch(url, { method: "DELETE", headers: authHeaders() })
 *
 * Prefer the api client for new code; this is for the legacy raw-fetch
 * call sites.
 */
import { TOKEN_KEY } from "@/lib/auth";

export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const t = localStorage.getItem(TOKEN_KEY);
  return { ...(extra ?? {}), ...(t ? { Authorization: `Bearer ${t}` } : {}) };
}
