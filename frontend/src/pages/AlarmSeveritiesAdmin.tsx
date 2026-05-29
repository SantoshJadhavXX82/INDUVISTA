/**
 * Phase 14.6 — Alarm Severities admin page.
 *
 * Lives at /global/alarm-severities. Lets operators extend the alarm
 * severity vocabulary beyond the built-in 5 (critical/high/medium/
 * low/info) without touching code.
 *
 * Layout mirrors AlarmsRules:
 *   - Header with row count + "+ New severity" button
 *   - Inline expanding form for create/edit
 *   - Table sorted by rank with color swatch column
 *   - Per-row edit (label/color/rank) + delete buttons
 *
 * Protections:
 *   - System rows: code immutable; delete disabled
 *   - In-use rows: delete disabled (server enforces 409 either way)
 *   - Rank conflicts: server returns 409, surfaced as inline error
 */
import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { HelpTip } from "@/components/ui/help-tip";
import { help } from "@/lib/help-text";
import {
  Plus, Pencil, Trash2, X, Save, AlertTriangle, RefreshCw, Palette,
} from "lucide-react";

import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { useSeverities, SEVERITIES_QUERY_KEY } from "@/lib/useSeverities";
import type {
  AlarmSeverity, AlarmSeverityCreate, AlarmSeverityUpdate,
} from "@/types/alarmSeverities";

interface FormState {
  id: number | null;   // null when creating
  code: string;        // immutable on edit
  label: string;
  color_hex: string;
  rank: string;        // string so empty input is representable
}

const EMPTY_FORM: FormState = {
  id: null,
  code: "",
  label: "",
  color_hex: "#888888",
  rank: "",
};

export default function AlarmSeveritiesAdmin() {
  const qc = useQueryClient();
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const sevQuery = useSeverities();
  const rows = useMemo(() => sevQuery.data ?? [], [sevQuery.data]);

  // ---- mutations ----

  const createMutation = useMutation({
    mutationFn: (payload: AlarmSeverityCreate) =>
      api.post<AlarmSeverity>("/alarms/severities", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SEVERITIES_QUERY_KEY });
      closeForm();
    },
    onError: (e: unknown) => setFormError(errorText(e)),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: AlarmSeverityUpdate }) =>
      api.patch<AlarmSeverity>(`/alarms/severities/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SEVERITIES_QUERY_KEY });
      closeForm();
    },
    onError: (e: unknown) => setFormError(errorText(e)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/alarms/severities/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: SEVERITIES_QUERY_KEY }),
  });

  // ---- form helpers ----

  const openCreate = () => {
    // Default new severity to one past the highest existing rank
    const maxRank = rows.length > 0
      ? Math.max(...rows.map((r) => r.rank))
      : 0;
    setForm({ ...EMPTY_FORM, rank: String(maxRank + 1) });
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (sev: AlarmSeverity) => {
    setForm({
      id: sev.id,
      code: sev.code,
      label: sev.label,
      color_hex: sev.color_hex,
      rank: String(sev.rank),
    });
    setFormError(null);
    setFormOpen(true);
  };

  const closeForm = () => {
    setFormOpen(false);
    setFormError(null);
  };

  const handleSubmit = () => {
    setFormError(null);

    const editing = form.id != null;
    const rank = parseInt(form.rank, 10);
    if (!isFinite(rank) || rank < 1 || rank > 1000) {
      return setFormError("Rank must be an integer between 1 and 1000.");
    }
    if (!form.label.trim()) {
      return setFormError("Label is required.");
    }
    if (!/^#[0-9a-fA-F]{6}$/.test(form.color_hex)) {
      return setFormError("Color must be a hex value like #dc2626.");
    }

    if (editing) {
      // PATCH excludes code (immutable)
      updateMutation.mutate({
        id: form.id!,
        body: {
          label: form.label.trim(),
          color_hex: form.color_hex,
          rank,
        },
      });
    } else {
      if (!/^[a-z][a-z0-9_]*$/.test(form.code)) {
        return setFormError(
          "Code must start with a lowercase letter and contain only " +
          "lowercase letters, digits, and underscores."
        );
      }
      createMutation.mutate({
        code: form.code,
        label: form.label.trim(),
        color_hex: form.color_hex,
        rank,
      });
    }
  };

  const handleDelete = (sev: AlarmSeverity) => {
    if (sev.is_system) return;            // server would reject anyway
    if (sev.in_use_count > 0) return;
    if (!confirm(`Delete severity "${sev.label}" (${sev.code})?`)) return;
    deleteMutation.mutate(sev.id);
  };

  // ---- render ----

  return (
    <div className="flex flex-col gap-4 p-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center justify-between gap-3 flex-wrap">
            <span className="flex items-center gap-2">
              <Palette className="h-4 w-4 text-muted-foreground" />
              Alarm Severities
              <span className="text-xs text-muted-foreground font-normal">
                {rows.length} configured
              </span>
              {(sevQuery.isFetching ||
                updateMutation.isPending ||
                deleteMutation.isPending) && (
                <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />
              )}
            </span>
            {!formOpen && (
              <Button size="sm" variant="outline" className="h-7 text-xs gap-1"
                      onClick={openCreate}>
                <Plus className="h-3 w-3" />
                New severity
              </Button>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {formOpen && (
            <SeverityForm
              form={form}
              onChange={setForm}
              error={formError}
              saving={createMutation.isPending || updateMutation.isPending}
              onSubmit={handleSubmit}
              onCancel={closeForm}
            />
          )}

          {sevQuery.isLoading && (
            <p className="text-xs text-muted-foreground py-6 px-3">
              Loading severities…
            </p>
          )}

          {sevQuery.isError && (
            <div className="flex items-start gap-2 text-xs text-destructive py-3 px-3">
              <AlertTriangle className="h-4 w-4 flex-shrink-0" />
              <span>Failed to load: {(sevQuery.error as Error)?.message}</span>
            </div>
          )}

          {rows.length > 0 && (
            <div className="overflow-x-auto border-t border-border">
              <table className="w-full text-xs">
                <thead className="bg-secondary/40 text-[10px] uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="text-right px-3 py-2 font-medium w-12"><span className="inline-flex items-center">Rank<HelpTip entry={help.severity.rank} /></span></th>
                    <th className="text-left px-3 py-2 font-medium w-12"><span className="inline-flex items-center">Color<HelpTip entry={help.severity.color_hex} /></span></th>
                    <th className="text-left px-3 py-2 font-medium"><span className="inline-flex items-center">Code<HelpTip entry={help.severity.code} /></span></th>
                    <th className="text-left px-3 py-2 font-medium">Label</th>
                    <th className="text-center px-3 py-2 font-medium">Type</th>
                    <th className="text-right px-3 py-2 font-medium">In use</th>
                    <th className="text-right px-3 py-2 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => (
                    <SeverityRow
                      key={s.id}
                      sev={s}
                      onEdit={() => openEdit(s)}
                      onDelete={() => handleDelete(s)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <p className="text-[11px] text-muted-foreground px-1">
        System severities are seeded with the system and cannot be deleted,
        but their label / color / rank can be customised. Custom severities
        can be added freely and assigned to rules from the Rule form on the
        Alarms page.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function SeverityRow({
  sev, onEdit, onDelete,
}: {
  sev: AlarmSeverity;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const deleteBlocked = sev.is_system || sev.in_use_count > 0;
  const deleteReason = sev.is_system
    ? "System severity — cannot delete"
    : sev.in_use_count > 0
      ? `Used by ${sev.in_use_count} rule${sev.in_use_count === 1 ? "" : "s"}`
      : "Delete";

  return (
    <tr className="border-t border-border hover:bg-secondary/30">
      <td className="px-3 py-1.5 text-right tabular-nums font-medium">
        {sev.rank}
      </td>
      <td className="px-3 py-1.5">
        <div
          className="w-5 h-5 rounded border border-border"
          style={{ backgroundColor: sev.color_hex }}
          title={sev.color_hex}
        />
      </td>
      <td className="px-3 py-1.5 font-mono text-[11px]">{sev.code}</td>
      <td className="px-3 py-1.5">{sev.label}</td>
      <td className="px-3 py-1.5 text-center">
        {sev.is_system ? (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 border border-slate-300">
            system
          </span>
        ) : (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-300">
            custom
          </span>
        )}
      </td>
      <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">
        {sev.in_use_count}
      </td>
      <td className="px-3 py-1.5 text-right">
        <div className="inline-flex items-center gap-1">
          <button
            type="button"
            onClick={onEdit}
            className="p-1 rounded hover:bg-secondary/60 text-muted-foreground hover:text-foreground"
            title="Edit"
          >
            <Pencil className="h-3 w-3" />
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={deleteBlocked}
            className="p-1 rounded hover:bg-red-100 text-muted-foreground hover:text-red-700 disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-muted-foreground disabled:cursor-not-allowed"
            title={deleteReason}
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Inline create/edit form
// ---------------------------------------------------------------------------

function SeverityForm({
  form, onChange, error, saving, onSubmit, onCancel,
}: {
  form: FormState;
  onChange: (f: FormState) => void;
  error: string | null;
  saving: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    onChange({ ...form, [k]: v });

  const editing = form.id != null;

  return (
    <div className="border-b border-border bg-secondary/20 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium">
          {editing ? `Edit severity '${form.code}'` : "New severity"}
        </h3>
        <button type="button" onClick={onCancel}
                className="text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        {/* Code (only editable when creating) */}
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Code</span>
          <Input
            value={form.code}
            onChange={(e) => set("code", e.target.value)}
            placeholder="e.g. warning, emergency_stop"
            disabled={editing}
            className="h-8 text-xs font-mono"
            maxLength={50}
          />
          {editing ? (
            <span className="text-[10px] text-muted-foreground">
              Code is immutable.
            </span>
          ) : (
            <span className="text-[10px] text-muted-foreground">
              Lowercase letters, digits, underscores. Must start with a letter.
            </span>
          )}
        </label>

        {/* Label */}
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Label</span>
          <Input
            value={form.label}
            onChange={(e) => set("label", e.target.value)}
            placeholder="e.g. Warning, Emergency Stop"
            className="h-8 text-xs"
            maxLength={100}
          />
        </label>

        {/* Color: native picker + hex input */}
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Color</span>
          <div className="flex items-center gap-2">
            <input
              type="color"
              value={form.color_hex}
              onChange={(e) => set("color_hex", e.target.value)}
              className="h-8 w-12 rounded border border-border cursor-pointer bg-transparent"
            />
            <Input
              value={form.color_hex}
              onChange={(e) => set("color_hex", e.target.value)}
              placeholder="#dc2626"
              className="h-8 text-xs font-mono"
              maxLength={7}
            />
          </div>
        </label>

        {/* Rank */}
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground"
                title="Lower rank = more urgent (1 is most urgent)">
            Rank
          </span>
          <Input
            value={form.rank}
            onChange={(e) => set("rank", e.target.value)}
            placeholder="6"
            inputMode="numeric"
            className="h-8 text-xs"
          />
          <span className="text-[10px] text-muted-foreground">
            1 = most urgent. Must be unique.
          </span>
        </label>
      </div>

      {error && (
        <div className="mt-3 flex items-start gap-2 text-xs text-destructive">
          <AlertTriangle className="h-4 w-4 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="mt-4 flex items-center justify-end gap-2">
        <Button variant="outline" size="sm" className="h-7 text-xs"
                onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button size="sm" className="h-7 text-xs gap-1"
                onClick={onSubmit} disabled={saving}>
          {saving ? (
            <RefreshCw className="h-3 w-3 animate-spin" />
          ) : (
            <Save className="h-3 w-3" />
          )}
          {editing ? "Save changes" : "Create severity"}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error formatter — FastAPI sends { detail: "..." } as ApiError
// ---------------------------------------------------------------------------

function errorText(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "object" && e && "detail" in e) {
    const d = (e as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    return JSON.stringify(d);
  }
  return String(e);
}
