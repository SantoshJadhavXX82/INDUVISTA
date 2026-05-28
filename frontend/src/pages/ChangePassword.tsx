/**
 * Standalone change-password page (Phase 21). Reached from the account menu.
 * Distinct from the forced first-login change inside Login.tsx — this is the
 * voluntary "change my password" any user can do anytime.
 */
import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function ChangePassword() {
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (next.length < 8) { setError("New password must be at least 8 characters."); return; }
    if (next !== confirm) { setError("Passwords do not match."); return; }
    setBusy(true);
    try {
      await api.post("/auth/change-password", {
        current_password: current,
        new_password: next,
      });
      setDone(true);
      setTimeout(() => navigate("/dashboard", { replace: true }), 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-md">
      <h1 className="text-2xl font-semibold mb-1" style={{ color: "var(--text-primary)" }}>
        Change password
      </h1>
      <p className="text-sm mb-6" style={{ color: "var(--text-secondary)" }}>
        Update the password for your account.
      </p>

      {done ? (
        <p className="text-sm" style={{ color: "var(--ios-green, #34c759)" }}>
          Password changed. Redirecting…
        </p>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <div>
            <Label htmlFor="cur">Current password</Label>
            <Input id="cur" type="password" value={current}
              onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
          </div>
          <div>
            <Label htmlFor="new">New password</Label>
            <Input id="new" type="password" value={next}
              onChange={(e) => setNext(e.target.value)} autoComplete="new-password" required />
          </div>
          <div>
            <Label htmlFor="cf">Confirm new password</Label>
            <Input id="cf" type="password" value={confirm}
              onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
          </div>
          {error && <p className="text-sm" style={{ color: "var(--ios-red)" }}>{error}</p>}
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Saving…" : "Change password"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => navigate(-1)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
