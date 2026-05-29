/**
 * User management (Phase 21 — admin only).
 *
 * CRUD over /api/admin/users (backend built in Phase 21). Lets an admin:
 *   - list users (role, status, last login)
 *   - create a user (local password, or pre-provision an ldap/os user)
 *   - change role / enable-disable
 *   - reset a user's password (forces change on next login)
 *   - disable (soft) a user
 *
 * The page is gated by RequireAuth minRole="admin" in App.tsx, and the
 * backend independently enforces admin on every endpoint — defense in depth.
 */
import { useState } from "react";
import { HelpTip } from "@/components/ui/help-tip";
import { help } from "@/lib/help-text";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { UserPlus, KeyRound, Ban, CheckCircle2, Trash2, AlertTriangle, HelpCircle, ChevronDown, ChevronUp, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from "@/components/ui/table";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

type User = {
  id: number;
  username: string;
  auth_provider: string;
  role: string;
  full_name: string | null;
  email: string | null;
  is_enabled: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
};

const ROLES = ["viewer", "operator", "engineer", "admin"] as const;
const PROVIDERS = ["local", "ldap", "os"] as const;

function roleBadgeVariant(role: string): "default" | "outline" | "success" | "warning" {
  if (role === "admin") return "warning";
  if (role === "engineer") return "success";
  return "outline";
}

export default function Users() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [showRoles, setShowRoles] = useState(false);
  const [resetFor, setResetFor] = useState<User | null>(null);
  const [deleteFor, setDeleteFor] = useState<User | null>(null);
  const [banner, setBanner] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  function flash(kind: "ok" | "err", text: string) {
    setBanner({ kind, text });
    setTimeout(() => setBanner(null), 3000);
  }

  const usersQ = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => api.get<User[]>("/admin/users"),
  });

  const updateRole = useMutation({
    mutationFn: ({ id, role }: { id: number; role: string }) =>
      api.patch<User>(`/admin/users/${id}`, { role }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin-users"] }); flash("ok", "Role updated."); },
    onError: (e: ApiError) => flash("err", e.detail),
  });

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enable }: { id: number; enable: boolean }) =>
      enable
        ? api.patch<User>(`/admin/users/${id}`, { is_enabled: true })
        : api.delete(`/admin/users/${id}`),
    onSuccess: (_d, v) => { qc.invalidateQueries({ queryKey: ["admin-users"] }); flash("ok", v.enable ? "User enabled." : "User disabled."); },
    onError: (e: ApiError) => flash("err", e.detail),
  });

  const deleteUser = useMutation({
    // ?hard=true permanently removes the row (audit history is preserved
    // because the audit log stores the username string, not a FK).
    mutationFn: (id: number) => api.delete(`/admin/users/${id}?hard=true`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin-users"] }); flash("ok", "User permanently deleted."); setDeleteFor(null); },
    onError: (e: ApiError) => { flash("err", e.detail); setDeleteFor(null); },
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold" style={{ color: "var(--text-primary)" }}>Users</h1>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Manage accounts, roles, and access. Admin only.
          </p>
        </div>
        <div className="flex items-center gap-2">
        <Button variant="outline" onClick={() => setShowRoles((s) => !s)} className="gap-2">
          <HelpCircle className="h-4 w-4" /> Role reference
          {showRoles ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </Button>
        <Button onClick={() => setShowCreate(true)} className="gap-2">
          <UserPlus className="h-4 w-4" /> Add user
        </Button>
        </div>
      </div>

      {banner && (
        <div
          className="rounded-lg px-3 py-2 text-sm"
          style={{
            backgroundColor: banner.kind === "ok" ? "var(--bg-grouped)" : "rgba(255,59,48,0.1)",
            color: banner.kind === "ok" ? "var(--text-primary)" : "var(--ios-red)",
            border: "0.5px solid var(--separator)",
          }}
        >
          {banner.text}
        </div>
      )}

      {showRoles && <RoleReferenceCard />}

      <Card>
        <CardContent className="pt-6">
      {usersQ.isLoading && <p className="text-sm" style={{ color: "var(--text-secondary)" }}>Loading…</p>}
          {usersQ.isError && <p className="text-sm" style={{ color: "var(--ios-red)" }}>Failed to load users.</p>}
          {usersQ.data && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead><span className="inline-flex items-center">Username<HelpTip entry={help.user.username} /></span></TableHead>
                  <TableHead><span className="inline-flex items-center">Role<HelpTip entry={help.user.role} /></span></TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last login</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usersQ.data.map((u) => (
                  <TableRow key={u.id} style={{ opacity: u.is_enabled ? 1 : 0.55 }}>
                    <TableCell className="font-medium">
                      {u.username}
                      {u.full_name && (
                        <span className="block text-xs" style={{ color: "var(--text-secondary)" }}>{u.full_name}</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <select
                        value={u.role}
                        onChange={(e) => updateRole.mutate({ id: u.id, role: e.target.value })}
                        disabled={!u.is_enabled}
                        className="rounded-md px-2 py-1 text-sm"
                        style={{ backgroundColor: "var(--bg-elevated)", border: "0.5px solid var(--separator)", color: "var(--text-primary)" }}
                      >
                        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-[10px] uppercase">{u.auth_provider}</Badge>
                    </TableCell>
                    <TableCell>
                      {u.is_enabled ? (
                        <Badge variant="success" className="gap-1"><CheckCircle2 className="h-3 w-3" />enabled</Badge>
                      ) : (
                        <Badge variant="outline" className="text-muted-foreground">disabled</Badge>
                      )}
                      {u.must_change_password && u.is_enabled && (
                        <Badge variant="warning" className="ml-1 text-[10px]">must change pw</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>
                      {u.last_login_at ? u.last_login_at.replace("T", " ").slice(0, 19) : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {u.auth_provider === "local" && (
                          <Button variant="ghost" size="sm" onClick={() => setResetFor(u)} title="Reset password" className="h-7 px-2">
                            <KeyRound className="h-4 w-4" />
                          </Button>
                        )}
                        {u.is_enabled ? (
                          <Button variant="ghost" size="sm" onClick={() => toggleEnabled.mutate({ id: u.id, enable: false })} title="Disable user" className="h-7 px-2" style={{ color: "var(--ios-red)" }}>
                            <Ban className="h-4 w-4" />
                          </Button>
                        ) : (
                          <Button variant="ghost" size="sm" onClick={() => toggleEnabled.mutate({ id: u.id, enable: true })} title="Enable user" className="h-7 px-2">
                            <CheckCircle2 className="h-4 w-4" />
                          </Button>
                        )}
                        <Button variant="ghost" size="sm" onClick={() => setDeleteFor(u)} title="Delete permanently" className="h-7 px-2" style={{ color: "var(--ios-red)" }}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {showCreate && (
        <CreateUserModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); qc.invalidateQueries({ queryKey: ["admin-users"] }); flash("ok", "User created."); }}
          onError={(msg) => flash("err", msg)}
        />
      )}
      {resetFor && (
        <ResetPasswordModal
          user={resetFor}
          onClose={() => setResetFor(null)}
          onDone={() => { setResetFor(null); flash("ok", "Password reset; user must change it on next login."); }}
          onError={(msg) => flash("err", msg)}
        />
      )}
      {deleteFor && (
        <DeleteUserModal
          user={deleteFor}
          busy={deleteUser.isPending}
          onClose={() => setDeleteFor(null)}
          onConfirm={() => deleteUser.mutate(deleteFor.id)}
        />
      )}
    </div>
  );
}

function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ backgroundColor: "rgba(0,0,0,0.35)" }} onClick={onClose}>
      <div
        className="w-full max-w-md rounded-2xl p-6"
        style={{ backgroundColor: "var(--bg-elevated)", border: "0.5px solid var(--separator)", boxShadow: "0 12px 40px rgba(0,0,0,0.2)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>{title}</h2>
          <button onClick={onClose}><X className="h-5 w-5" style={{ color: "var(--text-secondary)" }} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

function CreateUserModal({ onClose, onCreated, onError }: { onClose: () => void; onCreated: () => void; onError: (m: string) => void }) {
  const [username, setUsername] = useState("");
  const [role, setRole] = useState("viewer");
  const [provider, setProvider] = useState("local");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        username, role, auth_provider: provider,
        full_name: fullName || null, email: email || null,
        must_change_password: provider === "local",
      };
      if (provider === "local") body.password = password;
      await api.post("/admin/users", body);
      onCreated();
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : "Could not create user.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalShell title="Add user" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <div>
          <Label htmlFor="u">Username</Label>
          <Input id="u" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="r">Role</Label>
            <select id="r" value={role} onChange={(e) => setRole(e.target.value)}
              className="w-full rounded-md px-2 py-2 text-sm" style={{ backgroundColor: "var(--bg-elevated)", border: "0.5px solid var(--separator)", color: "var(--text-primary)" }}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div>
            <Label htmlFor="p">Auth provider</Label>
            <select id="p" value={provider} onChange={(e) => setProvider(e.target.value)}
              className="w-full rounded-md px-2 py-2 text-sm" style={{ backgroundColor: "var(--bg-elevated)", border: "0.5px solid var(--separator)", color: "var(--text-primary)" }}>
              {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        </div>
        {provider === "local" ? (
          <div>
            <Label htmlFor="pw">Initial password</Label>
            <Input id="pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required />
            <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>
              User must change this on first login. Min 8 characters.
            </p>
          </div>
        ) : (
          <p className="text-xs rounded-md px-2 py-2" style={{ color: "var(--text-secondary)", backgroundColor: "var(--bg-grouped)" }}>
            {provider.toUpperCase()} users are verified by the external identity provider — no password is stored here.
            (Provider integration is a future phase; this pre-provisions the role.)
          </p>
        )}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="fn">Full name (optional)</Label>
            <Input id="fn" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="em">Email (optional)</Label>
            <Input id="em" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
        </div>
        <div className="flex gap-2 pt-2">
          <Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create user"}</Button>
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </ModalShell>
  );
}

function ResetPasswordModal({ user, onClose, onDone, onError }: { user: User; onClose: () => void; onDone: () => void; onError: (m: string) => void }) {
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post(`/admin/users/${user.id}/reset-password`, { new_password: pw });
      onDone();
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : "Could not reset password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalShell title={`Reset password — ${user.username}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <div>
          <Label htmlFor="np">New password</Label>
          <Input id="np" type="password" value={pw} onChange={(e) => setPw(e.target.value)} minLength={8} autoFocus required />
          <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>
            The user will be forced to change it on their next login. Min 8 characters.
          </p>
        </div>
        <div className="flex gap-2 pt-2">
          <Button type="submit" disabled={busy}>{busy ? "Resetting…" : "Reset password"}</Button>
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </ModalShell>
  );
}

function DeleteUserModal({ user, busy, onClose, onConfirm }: { user: User; busy: boolean; onClose: () => void; onConfirm: () => void }) {
  const [typed, setTyped] = useState("");
  const confirmed = typed === user.username;
  return (
    <ModalShell title="Delete user permanently" onClose={onClose}>
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-lg px-3 py-3" style={{ backgroundColor: "rgba(255,59,48,0.08)" }}>
          <AlertTriangle className="h-5 w-5 shrink-0" style={{ color: "var(--ios-red)" }} />
          <p className="text-sm" style={{ color: "var(--text-primary)" }}>
            This permanently removes <strong>{user.username}</strong>. This cannot be undone.
            Their past actions remain in the audit log. To disable instead of delete,
            use the block button — that keeps the account and can be re-enabled later.
          </p>
        </div>
        <div>
          <Label htmlFor="confirm-del">Type the username to confirm</Label>
          <Input id="confirm-del" value={typed} onChange={(e) => setTyped(e.target.value)}
            placeholder={user.username} autoFocus autoComplete="off" />
        </div>
        <div className="flex gap-2">
          <Button onClick={onConfirm} disabled={!confirmed || busy}
            style={{ backgroundColor: confirmed ? "var(--ios-red)" : undefined }}>
            {busy ? "Deleting…" : "Delete permanently"}
          </Button>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </ModalShell>
  );
}

// ---------------------------------------------------------------------------
// Role reference — the live RBAC policy as a capability matrix. Roles are a
// privilege ladder: each inherits everything below it (viewer < operator <
// engineer < admin). This mirrors backend/app/auth/rbac_middleware.py.
// ---------------------------------------------------------------------------
const ROLE_CAPS: { area: string; v: boolean; o: boolean; e: boolean; a: boolean }[] = [
  { area: "Log in / view own identity", v: true, o: true, e: true, a: true },
  { area: "Change own password", v: true, o: true, e: true, a: true },
  { area: "View dashboards, trends, live data", v: true, o: true, e: true, a: true },
  { area: "View tags, devices, config (read)", v: true, o: true, e: true, a: true },
  { area: "View diagnostics, health, gaps", v: true, o: true, e: true, a: true },
  { area: "View alarms, audit log", v: true, o: true, e: true, a: true },
  { area: "View settings, shifts", v: true, o: true, e: true, a: true },
  { area: "Acknowledge alarms", v: false, o: true, e: true, a: true },
  { area: "Write command / setpoint tags (Modbus)", v: false, o: true, e: true, a: true },
  { area: "Create / edit / delete devices", v: false, o: false, e: true, a: true },
  { area: "Create / edit tags, register blocks", v: false, o: false, e: true, a: true },
  { area: "Configure channels / networks", v: false, o: false, e: true, a: true },
  { area: "Configure alarm rules, calc blocks", v: false, o: false, e: true, a: true },
  { area: "Configure OPC sources", v: false, o: false, e: true, a: true },
  { area: "Edit settings, shifts, units, groups", v: false, o: false, e: true, a: true },
  { area: "Manage users (create / role / disable / delete)", v: false, o: false, e: false, a: true },
  { area: "Manage API keys", v: false, o: false, e: false, a: true },
];

function Cap({ on }: { on: boolean }) {
  return on
    ? <CheckCircle2 className="h-4 w-4 mx-auto" style={{ color: "var(--ios-green, #34c759)" }} />
    : <span className="block text-center" style={{ color: "var(--text-tertiary, #b0b0b0)" }}>—</span>;
}

function RoleReferenceCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Role reference</CardTitle>
        <CardDescription>
          What each role can do. Roles are a privilege ladder — each inherits everything
          below it: <strong>viewer</strong> &lt; <strong>operator</strong> &lt;{" "}
          <strong>engineer</strong> &lt; <strong>admin</strong>. The backend enforces this
          on every request; the UI mirrors it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Capability</TableHead>
              <TableHead className="text-center w-20">Viewer</TableHead>
              <TableHead className="text-center w-20">Operator</TableHead>
              <TableHead className="text-center w-20">Engineer</TableHead>
              <TableHead className="text-center w-20">Admin</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {ROLE_CAPS.map((c) => (
              <TableRow key={c.area}>
                <TableCell className="text-sm">{c.area}</TableCell>
                <TableCell><Cap on={c.v} /></TableCell>
                <TableCell><Cap on={c.o} /></TableCell>
                <TableCell><Cap on={c.e} /></TableCell>
                <TableCell><Cap on={c.a} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="mt-4 grid gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
          <div><strong>Viewer</strong> — read-only observer: dashboards, trends, alarms, config (no changes).</div>
          <div><strong>Operator</strong> — viewer + runs the plant: acknowledge alarms, write setpoints / commands.</div>
          <div><strong>Engineer</strong> — operator + configures the system: devices, tags, alarms, OPC, settings.</div>
          <div><strong>Admin</strong> — engineer + manages users and API keys.</div>
        </div>
      </CardContent>
    </Card>
  );
}