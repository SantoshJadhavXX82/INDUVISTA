/**
 * Auth context (Phase 21 frontend).
 *
 * Holds the JWT + current user, persists the token in localStorage so a
 * page refresh stays logged in, and exposes login()/logout(). The api
 * client (lib/api.ts) reads the token from the same localStorage key on
 * every request, so there's a single source of truth.
 *
 * Roles drive UI: useAuth().hasRole("engineer") gates buttons/pages.
 */
import {
  createContext,
  useContext,
  useState,
  useCallback,
  type ReactNode,
} from "react";

export const TOKEN_KEY = "induvista:token";
export const USER_KEY = "induvista:user";

export type Role = "viewer" | "operator" | "engineer" | "admin";
const ROLE_ORDER: Role[] = ["viewer", "operator", "engineer", "admin"];

export type AuthUser = {
  username: string;
  role: Role;
};

type LoginResult = {
  access_token: string;
  username: string;
  role: Role;
  must_change_password: boolean;
};

type AuthContextValue = {
  user: AuthUser | null;
  token: string | null;
  isAuthenticated: boolean;
  mustChangePassword: boolean;
  login: (username: string, password: string) => Promise<LoginResult>;
  logout: () => void;
  hasRole: (min: Role) => boolean;
  clearMustChange: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function readStoredUser(): AuthUser | null {
  try {
    const raw = window.localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(
    () => window.localStorage.getItem(TOKEN_KEY),
  );
  const [user, setUser] = useState<AuthUser | null>(readStoredUser);
  const [mustChangePassword, setMustChange] = useState(false);

  const login = useCallback(async (username: string, password: string) => {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      let detail = "Login failed.";
      try {
        const j = await res.json();
        if (j?.detail && typeof j.detail === "string") detail = j.detail;
      } catch { /* keep default */ }
      throw new Error(detail);
    }
    const data = (await res.json()) as LoginResult;
    const u: AuthUser = { username: data.username, role: data.role };
    window.localStorage.setItem(TOKEN_KEY, data.access_token);
    window.localStorage.setItem(USER_KEY, JSON.stringify(u));
    setToken(data.access_token);
    setUser(u);
    setMustChange(data.must_change_password);
    return data;
  }, []);

  const logout = useCallback(() => {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setMustChange(false);
  }, []);

  const hasRole = useCallback(
    (min: Role) => {
      if (!user) return false;
      return ROLE_ORDER.indexOf(user.role) >= ROLE_ORDER.indexOf(min);
    },
    [user],
  );

  const clearMustChange = useCallback(() => setMustChange(false), []);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isAuthenticated: !!token,
        mustChangePassword,
        login,
        logout,
        hasRole,
        clearMustChange,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
