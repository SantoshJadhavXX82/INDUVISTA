/**
 * Phase 17.0a - Manage Computed Devices modal.
 *
 * Opened from CalcDefinitionsAdmin via the "Manage Computed Devices"
 * button. Lists existing devices with toggle/edit/delete actions and
 * an inline "+ Add device" expanding form at the top.
 *
 * Migration 0042 hotfix: removed the channel selector. All computed
 * devices live on the internal COMPUTED channel - the backend resolves
 * it automatically, the user never picks. The listing still shows
 * channel_name (always "COMPUTED") as a small static label so the user
 * knows where these devices live, but it's not editable.
 *
 * All mutations follow the universal ConfirmDialog pattern: a tap
 * opens the confirmation; the actual POST/PATCH/DELETE only fires on
 * confirm.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  X, Plus, Pencil, Trash2, Power, Loader2, AlertTriangle, Check, RefreshCw,
} from "lucide-react";

import {
  useComputedDevices, CALC_DEFINITIONS_QUERY_KEY,
} from "@/lib/useCalcDefinitions";
import type { ComputedDevice } from "@/types/calcDefinitions";
import { ConfirmDialog } from "@/components/ConfirmDialog";


interface ComputedDevicesModalProps {
  open: boolean;
  onClose: () => void;
}


export function ComputedDevicesModal({ open, onClose }: ComputedDevicesModalProps) {
  const devices = useComputedDevices();
  const qc = useQueryClient();

  // Form state for inline add/edit
  const [editingId, setEditingId] = useState<number | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formScanInterval, setFormScanInterval] = useState(1000);
  const [formError, setFormError] = useState<string | null>(null);

  // Confirmation state
  const [pendingToggle, setPendingToggle] = useState<ComputedDevice | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ComputedDevice | null>(null);

  // Reset when modal closes
  useEffect(() => {
    if (!open) {
      resetForm();
      setEditingId(null);
      setShowAddForm(false);
    }
  }, [open]);

  function resetForm() {
    setFormName("");
    setFormDescription("");
    setFormScanInterval(1000);
    setFormError(null);
  }

  function startEdit(d: ComputedDevice) {
    setEditingId(d.id);
    setShowAddForm(false);
    setFormName(d.name);
    setFormDescription(d.description ?? "");
    setFormScanInterval(d.scan_interval_ms);
    setFormError(null);
  }

  function startAdd() {
    setEditingId(null);
    setShowAddForm(true);
    resetForm();
  }

  function cancelForm() {
    setEditingId(null);
    setShowAddForm(false);
    resetForm();
  }

  // Mutations
  const createMutation = useMutation({
    mutationFn: async () => {
      const body = {
        name: formName.trim(),
        description: formDescription.trim() || null,
        scan_interval_ms: formScanInterval,
        enabled: true,
      };
      const res = await fetch("/api/computed-devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text || "create failed"}`);
      }
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["computed-devices"] });
      qc.invalidateQueries({ queryKey: CALC_DEFINITIONS_QUERY_KEY });
      cancelForm();
    },
    onError: (err: Error) => setFormError(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: async () => {
      if (editingId == null) throw new Error("no device selected");
      const body = {
        name: formName.trim(),
        description: formDescription.trim() || null,
        scan_interval_ms: formScanInterval,
      };
      const res = await fetch(`/api/computed-devices/${editingId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text || "update failed"}`);
      }
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["computed-devices"] });
      qc.invalidateQueries({ queryKey: CALC_DEFINITIONS_QUERY_KEY });
      cancelForm();
    },
    onError: (err: Error) => setFormError(err.message),
  });

  const toggleMutation = useMutation({
    mutationFn: async (d: ComputedDevice) => {
      const res = await fetch(`/api/computed-devices/${d.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !d.enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["computed-devices"] });
      qc.invalidateQueries({ queryKey: CALC_DEFINITIONS_QUERY_KEY });
    },
    onError: (err: Error) => alert(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async (d: ComputedDevice) => {
      const res = await fetch(`/api/computed-devices/${d.id}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) {
        throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["computed-devices"] });
      qc.invalidateQueries({ queryKey: CALC_DEFINITIONS_QUERY_KEY });
    },
    onError: (err: Error) => alert(err.message),
  });

  function handleSubmit() {
    setFormError(null);
    if (!formName.trim()) {
      setFormError("Name is required.");
      return;
    }
    if (editingId != null) {
      updateMutation.mutate();
    } else {
      createMutation.mutate();
    }
  }

  const sortedDevices = useMemo(() => {
    return [...(devices.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  }, [devices.data]);

  const busyForm = createMutation.isPending || updateMutation.isPending;
  const showForm = editingId != null || showAddForm;

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-start justify-center
                   bg-black/40 backdrop-blur-sm overflow-y-auto py-10"
        onClick={onClose}
      >
        <div
          className="bg-card border border-border rounded shadow-lg
                     w-full max-w-3xl mx-4 my-auto"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <h2 className="text-sm font-medium">Manage Computed Devices</h2>
            <button
              type="button"
              onClick={onClose}
              className="h-7 w-7 inline-flex items-center justify-center rounded
                         text-muted-foreground hover:bg-secondary"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Body */}
          <div className="p-4 space-y-3">
            <p className="text-[11px] text-muted-foreground">
              Computed Devices are virtual hosts for computed tags. They don't
              poll any network channel - the calc evaluator produces their tags'
              values on every tick. All Computed Devices live on a dedicated
              internal channel (<code className="text-[10px]">COMPUTED</code>);
              you don't need to pick one.
            </p>

            {!showForm && (
              <button
                type="button"
                onClick={startAdd}
                className="text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground
                           hover:bg-primary/90 inline-flex items-center gap-1.5"
              >
                <Plus className="h-3 w-3" />
                Add Computed Device
              </button>
            )}

            {/* Inline add/edit form */}
            {showForm && (
              <div className="border border-border rounded p-3 bg-secondary/10 space-y-2">
                <div className="flex items-center justify-between mb-1">
                  <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    {editingId != null ? `Edit device #${editingId}` : "New computed device"}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    Channel: <span className="font-mono">Computed (Internal)</span>
                  </div>
                </div>

                <div>
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                    Name <span className="text-destructive">*</span>
                  </label>
                  <input
                    type="text"
                    className="h-7 text-xs bg-card border border-border rounded px-2 w-full font-mono"
                    value={formName}
                    onChange={(e) => setFormName(e.target.value)}
                    placeholder="e.g. CALCS_PROCESS_AREA_1"
                  />
                </div>

                <div>
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                    Description
                  </label>
                  <input
                    type="text"
                    className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                    value={formDescription}
                    onChange={(e) => setFormDescription(e.target.value)}
                    placeholder="optional description"
                  />
                </div>

                <div>
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                    Evaluator scan interval (ms)
                  </label>
                  <input
                    type="number"
                    min={10}
                    className="h-7 text-xs bg-card border border-border rounded px-2 w-32"
                    value={formScanInterval}
                    onChange={(e) => setFormScanInterval(Number(e.target.value))}
                  />
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    Default for tags on this device. Each tag can override via its own execution_rate_ms.
                  </p>
                </div>

                {formError && (
                  <div className="flex items-start gap-2 text-xs text-destructive
                                  bg-destructive/10 border border-destructive/30
                                  rounded p-2">
                    <AlertTriangle className="h-3 w-3 flex-shrink-0 mt-0.5" />
                    <span className="font-mono whitespace-pre-wrap break-words">{formError}</span>
                  </div>
                )}

                <div className="flex items-center justify-end gap-2 pt-1">
                  <button
                    type="button"
                    onClick={cancelForm}
                    disabled={busyForm}
                    className="text-xs px-3 py-1 rounded border border-border
                               hover:bg-secondary disabled:opacity-30"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleSubmit}
                    disabled={busyForm}
                    className="text-xs px-3 py-1 rounded bg-primary text-primary-foreground
                               hover:bg-primary/90 disabled:opacity-30 inline-flex items-center gap-1.5"
                  >
                    {busyForm && <Loader2 className="h-3 w-3 animate-spin" />}
                    <Check className="h-3 w-3" />
                    {editingId != null ? "Save" : "Create"}
                  </button>
                </div>
              </div>
            )}

            {/* Device list */}
            {devices.isLoading ? (
              <div className="text-xs text-muted-foreground flex items-center gap-2 py-4">
                <RefreshCw className="h-3 w-3 animate-spin" />
                Loading…
              </div>
            ) : sortedDevices.length === 0 ? (
              <div className="text-xs text-muted-foreground italic text-center py-6 border border-dashed border-border rounded">
                No Computed Devices yet. Click "Add Computed Device" above to create one.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground text-[10px] uppercase tracking-wider">
                      <th className="text-right px-2 py-2 font-medium">ID</th>
                      <th className="text-left px-3 py-2 font-medium">Name</th>
                      <th className="text-right px-3 py-2 font-medium">Scan (ms)</th>
                      <th className="text-right px-3 py-2 font-medium">Tags</th>
                      <th className="text-center px-3 py-2 font-medium">Enabled</th>
                      <th className="text-center px-3 py-2 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedDevices.map((d) => (
                      <tr key={d.id} className="border-t border-border hover:bg-secondary/30">
                        <td className="px-2 py-1.5 text-right tabular-nums text-muted-foreground">{d.id}</td>
                        <td className="px-3 py-1.5">
                          <div className="font-medium">{d.name}</div>
                          {d.description && (
                            <div className="text-[10px] text-muted-foreground">{d.description}</div>
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums font-mono text-[11px]">{d.scan_interval_ms}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{d.computed_tag_count}</td>
                        <td className="px-3 py-1.5 text-center">
                          <span className={`inline-block text-[10px] px-1.5 py-0.5 rounded border
                              ${d.enabled
                                ? "bg-emerald-50 text-emerald-800 border-emerald-300"
                                : "bg-slate-100 text-slate-600 border-slate-300"}`}>
                            {d.enabled ? "on" : "off"}
                          </span>
                        </td>
                        <td className="px-3 py-1.5">
                          <div className="flex items-center justify-center gap-1">
                            <button
                              type="button"
                              onClick={() => setPendingToggle(d)}
                              disabled={toggleMutation.isPending}
                              title={d.enabled ? "Disable" : "Enable"}
                              className="h-6 w-6 inline-flex items-center justify-center rounded
                                         hover:bg-secondary disabled:opacity-30"
                            >
                              {toggleMutation.isPending && toggleMutation.variables?.id === d.id ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                <Power className={`h-3 w-3 ${
                                  d.enabled ? "text-emerald-600" : "text-muted-foreground opacity-40"
                                }`} />
                              )}
                            </button>
                            <button
                              type="button"
                              onClick={() => startEdit(d)}
                              title="Edit"
                              className="h-6 w-6 inline-flex items-center justify-center rounded
                                         hover:bg-secondary text-muted-foreground hover:text-foreground"
                            >
                              <Pencil className="h-3 w-3" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setPendingDelete(d)}
                              disabled={deleteMutation.isPending}
                              title="Delete"
                              className="h-6 w-6 inline-flex items-center justify-center rounded
                                         hover:bg-destructive/10 text-muted-foreground
                                         hover:text-destructive disabled:opacity-30"
                            >
                              {deleteMutation.isPending && deleteMutation.variables?.id === d.id ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                <Trash2 className="h-3 w-3" />
                              )}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border bg-secondary/10">
            <button
              type="button"
              onClick={onClose}
              className="text-xs px-3 py-1.5 rounded border border-border hover:bg-secondary"
            >
              Close
            </button>
          </div>
        </div>
      </div>

      {/* Toggle confirm */}
      <ConfirmDialog
        open={!!pendingToggle}
        title={pendingToggle?.enabled
          ? `Disable Computed Device #${pendingToggle?.id}?`
          : `Enable Computed Device #${pendingToggle?.id}?`}
        description={pendingToggle?.enabled ? (
          <>
            All <strong>{pendingToggle?.computed_tag_count}</strong> computed
            tag(s) on <strong>{pendingToggle?.name}</strong> will stop being
            evaluated. Existing history and last-written values are preserved.
            <br />
            <span className="text-muted-foreground">
              (Tags-level disable is separate; this disables the parent device only.)
            </span>
          </>
        ) : (
          <>Computed device <strong>{pendingToggle?.name}</strong> will be re-enabled. Tags on it will resume evaluation if individually enabled.</>
        )}
        confirmLabel={pendingToggle?.enabled ? "Disable" : "Enable"}
        severity={pendingToggle?.enabled ? "warning" : "normal"}
        busy={toggleMutation.isPending}
        onConfirm={() => {
          if (pendingToggle) {
            toggleMutation.mutate(pendingToggle, {
              onSettled: () => setPendingToggle(null),
            });
          }
        }}
        onCancel={() => setPendingToggle(null)}
      />

      {/* Delete confirm */}
      <ConfirmDialog
        open={!!pendingDelete}
        title={`Delete Computed Device "${pendingDelete?.name}"?`}
        description={
          <>
            This will <strong>permanently delete</strong>{" "}
            <strong>{pendingDelete?.computed_tag_count}</strong> computed tag(s)
            on this device, including their definitions, execution stats, and
            historical values.
            <br />
            <span className="text-destructive">This cannot be undone.</span>
          </>
        }
        confirmLabel="Delete device and all tags"
        severity="destructive"
        busy={deleteMutation.isPending}
        onConfirm={() => {
          if (pendingDelete) {
            deleteMutation.mutate(pendingDelete, {
              onSettled: () => setPendingDelete(null),
            });
          }
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}
