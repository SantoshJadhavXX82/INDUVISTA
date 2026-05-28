/**
 * Login page (Phase 21). Username/password form. On must_change_password,
 * shows a second step to set a new password before entering the app.
 *
 * Styling mirrors the app's iOS-ish tokens (var(--bg-*) etc.) and uses the
 * existing Button/Input/Label/Card primitives.
 */
import { useState } from "react";
import { useNavigate, useLocation, Navigate } from "react-router";
import { Activity } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function Login() {
  const { login, isAuthenticated, mustChangePassword, clearMustChange } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/dashboard";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Forced password-change step state.
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");

  // Already logged in (and no pending password change) → go to app.
  if (isAuthenticated && !mustChangePassword) {
    return <Navigate to={from} replace />;
  }

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result = await login(username, password);
      if (!result.must_change_password) {
        navigate(from, { replace: true });
      }
      // else: fall through to the change-password step (mustChangePassword=true)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPw.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    if (newPw !== confirmPw) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/change-password", {
        current_password: password,
        new_password: newPw,
      });
      clearMustChange();
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change password.");
    } finally {
      setBusy(false);
    }
  }

  const showChangeStep = isAuthenticated && mustChangePassword;

  return (
    <div
      className="flex h-screen items-center justify-center"
      style={{ backgroundColor: "var(--bg-grouped)" }}
    >
      <div
        className="w-full max-w-sm rounded-2xl p-8"
        style={{
          backgroundColor: "var(--bg-elevated)",
          border: "0.5px solid var(--separator)",
          boxShadow: "0 10px 40px rgba(0,0,0,0.12)",
        }}
      >
        <div className="mb-6 flex items-center gap-2">
          <Activity className="h-7 w-7" style={{ color: "var(--ios-blue)" }} />
          <div>
            <div className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
              InduVista
            </div>
            <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
              Industrial Reporting Tool
            </div>
          </div>
        </div>

        {!showChangeStep ? (
          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                required
              />
            </div>
            <div>
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
            {error && (
              <p className="text-sm" style={{ color: "var(--ios-red)" }}>{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        ) : (
          <form onSubmit={handleChangePassword} className="space-y-4">
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
              You must set a new password before continuing.
            </p>
            <div>
              <Label htmlFor="newpw">New password</Label>
              <Input
                id="newpw"
                type="password"
                value={newPw}
                onChange={(e) => setNewPw(e.target.value)}
                autoFocus
                autoComplete="new-password"
                required
              />
            </div>
            <div>
              <Label htmlFor="confirmpw">Confirm new password</Label>
              <Input
                id="confirmpw"
                type="password"
                value={confirmPw}
                onChange={(e) => setConfirmPw(e.target.value)}
                autoComplete="new-password"
                required
              />
            </div>
            {error && (
              <p className="text-sm" style={{ color: "var(--ios-red)" }}>{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Saving…" : "Set password & continue"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
