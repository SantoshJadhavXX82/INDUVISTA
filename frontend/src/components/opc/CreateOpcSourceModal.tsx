/**
 * OPC UA source create/edit modal — Phase OPC-web.3.
 *
 * Single dialog handles both modes:
 *   - existingSource == null  → create mode (all fields editable)
 *   - existingSource != null  → edit mode (name+endpoint locked; tuning
 *                                          knobs editable; password
 *                                          left blank means "don't
 *                                          change it" — empty string
 *                                          can't be distinguished from
 *                                          a deliberately-blank pw in
 *                                          a PATCH so this is the
 *                                          honest semantic)
 *
 * Why name+endpoint are locked in edit mode: changing them on a live
 * source would orphan tag mappings and synthetic device. If you need
 * to repoint a source at a different server, create a new source and
 * delete the old one. The backend doesn't prevent this — it's a
 * deliberate UX restraint to avoid footguns.
 *
 * The modal scaffolding (backdrop, esc handler, focus trap) follows
 * the same pattern as ConfirmDialog.tsx. We don't use shadcn/ui's
 * Dialog primitive because INDUVISTA's existing modals are hand-rolled
 * and the visual language is already established.
 */
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X, AlertTriangle, Loader2, Eye, EyeOff } from "lucide-react";

import { api } from "@/lib/api";
import type { OpcSourceCreate, OpcSourceResponse, OpcSourceUpdate } from "@/types/api";

export const OPC_SOURCES_QUERY_KEY = ["opc-sources"];

const SECURITY_POLICIES: OpcSourceResponse["security_policy"][] = [
  "None",
  "Basic128Rsa15",
  "Basic256",
  "Basic256Sha256",
  "Aes128_Sha256_RsaOaep",
  "Aes256_Sha256_RsaPss",
];

interface Props {
  open: boolean;
  onClose: () => void;
  /** Edit mode when set. */
  existingSource?: OpcSourceResponse | null;
}

export function CreateOpcSourceModal({ open, onClose, existingSource }: Props) {
  const isEdit = !!existingSource;
  const qc = useQueryClient();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [securityPolicy, setSecurityPolicy] =
    useState<OpcSourceResponse["security_policy"]>("None");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [publishingIntervalMs, setPublishingIntervalMs] = useState(1000);
  const [reconnectMinSec, setReconnectMinSec] = useState(1);
  const [reconnectMaxSec, setReconnectMaxSec] = useState(60);
  const [isEnabled, setIsEnabled] = useState(true);

  const [submitError, setSubmitError] = useState<string | null>(null);

  // Reset / hydrate form when modal opens.
  useEffect(() => {
    if (!open) return;
    if (existingSource) {
      setName(existingSource.name);
      setDescription(existingSource.description ?? "");
      setEndpoint(existingSource.endpoint);
      setSecurityPolicy(existingSource.security_policy);
      setUsername(existingSource.username);
      setPassword("");  // never pre-fill — backend never returns it
      setPublishingIntervalMs(existingSource.publishing_interval_ms);
      setReconnectMinSec(existingSource.reconnect_min_sec);
      setReconnectMaxSec(existingSource.reconnect_max_sec);
      setIsEnabled(existingSource.is_enabled);
    } else {
      setName("");
      setDescription("");
      setEndpoint("opc.tcp://");
      setSecurityPolicy("None");
      setUsername("");
      setPassword("");
      setPublishingIntervalMs(1000);
      setReconnectMinSec(1);
      setReconnectMaxSec(60);
      setIsEnabled(true);
    }
    setSubmitError(null);
    setShowPw(false);
  }, [open, existingSource]);

  // Esc to close.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        if (!mutation.isPending) onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, onClose]);

  const mutation = useMutation({
    mutationFn: async () => {
      if (isEdit && existingSource) {
        // PATCH — only send fields that actually changed, and never
        // send empty password (would clobber the existing one).
        const patch: OpcSourceUpdate = {};
        if (description !== (existingSource.description ?? ""))
          patch.description = description || null;
        if (securityPolicy !== existingSource.security_policy)
          patch.security_policy = securityPolicy;
        if (username !== existingSource.username) patch.username = username;
        if (password) patch.password = password;
        if (publishingIntervalMs !== existingSource.publishing_interval_ms)
          patch.publishing_interval_ms = publishingIntervalMs;
        if (reconnectMinSec !== existingSource.reconnect_min_sec)
          patch.reconnect_min_sec = reconnectMinSec;
        if (reconnectMaxSec !== existingSource.reconnect_max_sec)
          patch.reconnect_max_sec = reconnectMaxSec;
        if (isEnabled !== existingSource.is_enabled) patch.is_enabled = isEnabled;
        // If nothing changed, the backend returns 400 — short-circuit
        // here for a friendlier UX message.
        if (Object.keys(patch).length === 0) {
          throw new Error("No changes to save");
        }
        return api.patch<OpcSourceResponse>(
          `/opc-sources/${existingSource.id}`,
          patch,
        );
      } else {
        const body: OpcSourceCreate = {
          name,
          description: description || null,
          endpoint,
          security_policy: securityPolicy,
          username,
          password,
          publishing_interval_ms: publishingIntervalMs,
          reconnect_min_sec: reconnectMinSec,
          reconnect_max_sec: reconnectMaxSec,
          is_enabled: isEnabled,
        };
        return api.post<OpcSourceResponse>("/opc-sources", body);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: OPC_SOURCES_QUERY_KEY });
      onClose();
    },
    onError: (err: Error) => {
      setSubmitError(err.message);
    },
  });

  if (!open) return null;

  const canSubmit =
    !mutation.isPending &&
    name.trim().length > 0 &&
    endpoint.trim().startsWith("opc.tcp://") &&
    reconnectMaxSec >= reconnectMinSec;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center
                 bg-black/50 backdrop-blur-sm overflow-y-auto py-8"
      onClick={() => !mutation.isPending && onClose()}
      role="presentation"
    >
      <div
        className="bg-card border border-border rounded shadow-lg w-full max-w-xl mx-4 my-auto"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="opc-source-modal-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h2 id="opc-source-modal-title" className="text-sm font-medium">
            {isEdit ? `Edit OPC Source: ${existingSource!.name}` : "Add OPC UA Source"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={mutation.isPending}
            className="h-6 w-6 inline-flex items-center justify-center rounded
                       text-muted-foreground hover:bg-secondary disabled:opacity-30"
            aria-label="Close"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Body — scrollable in case viewport is short */}
        <div className="px-4 py-3 space-y-3 max-h-[70vh] overflow-y-auto">
          {/* Name */}
          <div>
            <label className="text-[11px] text-muted-foreground block mb-1">
              Name {!isEdit && <span className="text-destructive">*</span>}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={isEdit}
              placeholder="e.g. Plant-A-UA"
              maxLength={100}
              className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                         focus:outline-none focus:ring-2 focus:ring-primary
                         disabled:bg-secondary/30 disabled:text-muted-foreground"
            />
            {isEdit && (
              <p className="text-[10px] text-muted-foreground mt-1">
                Name is locked after creation — it links to the synthetic
                channel and device backing this source.
              </p>
            )}
          </div>

          {/* Description */}
          <div>
            <label className="text-[11px] text-muted-foreground block mb-1">
              Description
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this source for?"
              className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                         focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>

          {/* Endpoint */}
          <div>
            <label className="text-[11px] text-muted-foreground block mb-1">
              Endpoint URL {!isEdit && <span className="text-destructive">*</span>}
            </label>
            <input
              type="text"
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              disabled={isEdit}
              placeholder="opc.tcp://host.docker.internal:14840"
              maxLength={512}
              className="h-8 text-xs font-mono bg-card border border-border rounded px-2 w-full
                         focus:outline-none focus:ring-2 focus:ring-primary
                         disabled:bg-secondary/30 disabled:text-muted-foreground"
            />
            {isEdit && (
              <p className="text-[10px] text-muted-foreground mt-1">
                Endpoint locked after creation — to repoint, create a new
                source and delete this one.
              </p>
            )}
          </div>

          {/* Security policy */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[11px] text-muted-foreground block mb-1">
                Security policy
              </label>
              <select
                value={securityPolicy}
                onChange={(e) =>
                  setSecurityPolicy(e.target.value as typeof securityPolicy)
                }
                className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                           focus:outline-none focus:ring-2 focus:ring-primary"
              >
                {SECURITY_POLICIES.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
            <div className="flex items-end pb-1">
              <label className="text-xs flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={isEnabled}
                  onChange={(e) => setIsEnabled(e.target.checked)}
                  className="rounded"
                />
                Enabled (subscribed by worker)
              </label>
            </div>
          </div>

          {/* Auth */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[11px] text-muted-foreground block mb-1">
                Username
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="(blank = anonymous)"
                maxLength={128}
                className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                           focus:outline-none focus:ring-2 focus:ring-primary"
                autoComplete="off"
              />
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground block mb-1">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={isEdit ? "(leave blank to keep)" : "(blank = anonymous)"}
                  maxLength={256}
                  className="h-8 text-xs bg-card border border-border rounded px-2 w-full pr-8
                             focus:outline-none focus:ring-2 focus:ring-primary"
                  autoComplete="new-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((v) => !v)}
                  className="absolute right-1 top-1 h-6 w-6 inline-flex items-center
                             justify-center text-muted-foreground hover:text-foreground rounded"
                  tabIndex={-1}
                  aria-label={showPw ? "Hide password" : "Show password"}
                >
                  {showPw ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                </button>
              </div>
            </div>
          </div>

          {/* Tuning */}
          <div className="pt-2 border-t border-border">
            <p className="text-[11px] text-muted-foreground mb-2">Tuning</p>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-[10px] text-muted-foreground block mb-1">
                  Publishing interval (ms)
                </label>
                <input
                  type="number"
                  min={50}
                  max={60000}
                  step={50}
                  value={publishingIntervalMs}
                  onChange={(e) => setPublishingIntervalMs(Number(e.target.value))}
                  className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                             focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
              <div>
                <label className="text-[10px] text-muted-foreground block mb-1">
                  Reconnect min (s)
                </label>
                <input
                  type="number"
                  min={0.1}
                  step={0.5}
                  value={reconnectMinSec}
                  onChange={(e) => setReconnectMinSec(Number(e.target.value))}
                  className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                             focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
              <div>
                <label className="text-[10px] text-muted-foreground block mb-1">
                  Reconnect max (s)
                </label>
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={reconnectMaxSec}
                  onChange={(e) => setReconnectMaxSec(Number(e.target.value))}
                  className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                             focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
            </div>
            {reconnectMaxSec < reconnectMinSec && (
              <p className="text-[10px] text-destructive mt-1">
                Max must be ≥ min.
              </p>
            )}
          </div>

          {submitError && (
            <div className="flex items-start gap-2 text-[11px] text-destructive
                            bg-destructive/10 border border-destructive/30 rounded px-2 py-1.5">
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <div>{submitError}</div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border bg-secondary/10">
          <button
            type="button"
            onClick={onClose}
            disabled={mutation.isPending}
            className="text-xs px-3 py-1.5 rounded border border-border
                       hover:bg-secondary disabled:opacity-30 focus:outline-none
                       focus:ring-2 focus:ring-offset-1 focus:ring-primary"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={!canSubmit}
            className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground
                       hover:bg-primary/90 disabled:opacity-30 disabled:cursor-not-allowed
                       inline-flex items-center gap-1.5"
          >
            {mutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            {isEdit ? "Save changes" : "Create source"}
          </button>
        </div>
      </div>
    </div>
  );
}
