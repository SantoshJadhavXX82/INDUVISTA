/**
 * Phase 14.6b - Alarm Rule Types admin page.
 *
 * Lives at /global/alarm-types. Read-mostly view of the alarm rule
 * type vocabulary.
 *
 * Phase 14.12 update: the "New rule type" button has been removed.
 * Custom rule types added through this UI were always created with
 * is_evaluable=false (no Python evaluator existed for them), so
 * rules referencing them never fired - a silent footgun. New rule
 * types are now introduced exclusively via database migrations
 * paired with a Python evaluator class in
 * app/workers/alarm_evaluator.py (see Phase 14.10 frozen + spike
 * as the worked example).
 *
 * Operators can still edit label / description / rank of existing
 * rule types and delete custom (non-system, unused) ones - useful
 * for cleaning up legacy entries that may have been created before
 * this restriction landed.
 */
import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Pencil, Trash2, X, Save, AlertTriangle, RefreshCw,
  ListChecks, CheckCircle2, MinusCircle,
} from "lucide-react";

import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { useRuleTypes, RULE_TYPES_QUERY_KEY } from "@/lib/useRuleTypes";
import type {
  AlarmRuleType, AlarmRuleTypeUpdate,
} from "@/types/alarmRuleTypes";

interface FormState {
  id: number;
  code: string;
  label: string;
  description: string;
  rank: string;
}

export default function AlarmRuleTypesAdmin() {
  const qc = useQueryClient();
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const rtQuery = useRuleTypes();
  const rows = useMemo(() => rtQuery.data ?? [], [rtQuery.data]);

  // ---- mutations (edit + delete only; create removed in Phase 14.12) ----

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: AlarmRuleTypeUpdate }) =>
      api.patch<AlarmRuleType>(`/alarms/rule-types/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: RULE_TYPES_QUERY_KEY });
      closeForm();
    },
    onError: (e: unknown) => setFormError(errorText(e)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/alarms/rule-types/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: RULE_TYPES_QUERY_KEY }),
  });

  // ---- form helpers ----

  const openEdit = (rt: AlarmRuleType) => {
    setForm({
      id: rt.id,
      code: rt.code,
      label: rt.label,
      description: rt.description ?? "",
      rank: String(rt.rank),
    });
    setFormError(null);
    setFormOpen(true);
  };

  const closeForm = () => {
    setFormOpen(false);
    setFormError(null);
    setForm(null);
  };

  const handleSubmit = () => {
    if (!form) return;
    setFormError(null);

    const rank = parseInt(form.rank, 10);
    if (!isFinite(rank) || rank < 1 || rank > 1000) {
      return setFormError("Rank must be an integer between 1 and 1000.");
    }
    if (!form.label.trim()) {
      return setFormError("Label is required.");
    }

    updateMutation.mutate({
      id: form.id,
      body: {
        label: form.label.trim(),
        description: form.description.trim() || null,
        rank,
      },
    });
  };

  const handleDelete = (rt: AlarmRuleType) => {
    if (rt.is_system || rt.in_use_count > 0) return;
    if (!confirm(`Delete rule type "${rt.label}" (${rt.code})?`)) return;
    deleteMutation.mutate(rt.id);
  };

  return (
    <div className="flex flex-col gap-4 p-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center justify-between gap-3 flex-wrap">
            <span className="flex items-center gap-2">
              <ListChecks className="h-4 w-4 text-muted-foreground" />
              Alarm Rule Types
              <span className="text-xs text-muted-foreground font-normal">
                {rows.length} configured
              </span>
              {(rtQuery.isFetching ||
                updateMutation.isPending ||
                deleteMutation.isPending) && (
                <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />
              )}
            </span>
            {/* "New rule type" button removed in Phase 14.12 - new types ship via migrations. */}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {formOpen && form && (
            <RuleTypeForm
              form={form}
              onChange={setForm}
              error={formError}
              saving={updateMutation.isPending}
              onSubmit={handleSubmit}
              onCancel={closeForm}
            />
          )}

          {rtQuery.isLoading && (
            <p className="text-xs text-muted-foreground py-6 px-3">
              Loading rule types...
            </p>
          )}

          {rtQuery.isError && (
            <div className="flex items-start gap-2 text-xs text-destructive py-3 px-3">
              <AlertTriangle className="h-4 w-4 flex-shrink-0" />
              <span>Failed to load: {(rtQuery.error as Error)?.message}</span>
            </div>
          )}

          {rows.length > 0 && (
            <div className="overflow-x-auto border-t border-border">
              <table className="w-full text-xs">
                <thead className="bg-secondary/40 text-[10px] uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="text-right px-3 py-2 font-medium w-12">Rank</th>
                    <th className="text-left px-3 py-2 font-medium">Code</th>
                    <th className="text-left px-3 py-2 font-medium">Label</th>
                    <th className="text-left px-3 py-2 font-medium">Description</th>
                    <th className="text-center px-3 py-2 font-medium">Type</th>
                    <th className="text-center px-3 py-2 font-medium"
                        title="Whether the evaluator has logic for this type">
                      Evaluator
                    </th>
                    <th className="text-right px-3 py-2 font-medium">In use</th>
                    <th className="text-right px-3 py-2 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((rt) => (
                    <RuleTypeRow
                      key={rt.id}
                      rt={rt}
                      onEdit={() => openEdit(rt)}
                      onDelete={() => handleDelete(rt)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="px-1 text-[11px] text-muted-foreground space-y-1">
        <p>
          <strong>New rule types are introduced via database migrations</strong>{" "}
          paired with a Python evaluator class. Creating them through the
          UI was removed because rules using non-evaluable types never
          fire - it was an operator footgun. Edit / delete here is
          preserved for housekeeping of label / description / rank on
          existing rows.
        </p>
        <p>
          <strong>Evaluator</strong> status reflects whether the alarm
          evaluator has matching Python logic. Non-evaluable types are
          taxonomy-only; any rules referencing them will not fire.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function RuleTypeRow({
  rt, onEdit, onDelete,
}: {
  rt: AlarmRuleType;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const deleteBlocked = rt.is_system || rt.in_use_count > 0;
  const deleteReason = rt.is_system
    ? "System rule type - cannot delete"
    : rt.in_use_count > 0
      ? `Used by ${rt.in_use_count} rule${rt.in_use_count === 1 ? "" : "s"}`
      : "Delete";

  return (
    <tr className="border-t border-border hover:bg-secondary/30">
      <td className="px-3 py-1.5 text-right tabular-nums font-medium">
        {rt.rank}
      </td>
      <td className="px-3 py-1.5 font-mono text-[11px]">{rt.code}</td>
      <td className="px-3 py-1.5 font-medium">{rt.label}</td>
      <td className="px-3 py-1.5 text-muted-foreground max-w-[300px]"
          title={rt.description ?? ""}>
        <span className="line-clamp-2">{rt.description ?? "-"}</span>
      </td>
      <td className="px-3 py-1.5 text-center">
        {rt.is_system ? (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 border border-slate-300">
            system
          </span>
        ) : (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-300">
            custom
          </span>
        )}
      </td>
      <td className="px-3 py-1.5 text-center">
        {rt.is_evaluable ? (
          <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-800 border border-emerald-300"
                title="Evaluator has logic - rules of this type will fire when their conditions are met">
            <CheckCircle2 className="h-2.5 w-2.5" />
            evaluable
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 border border-amber-300"
                title="Evaluator has no logic for this type - rules can be configured but will never fire">
            <MinusCircle className="h-2.5 w-2.5" />
            inert
          </span>
        )}
      </td>
      <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">
        {rt.in_use_count}
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
// Inline edit form (create flow removed in Phase 14.12).
// ---------------------------------------------------------------------------

function RuleTypeForm({
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

  return (
    <div className="border-b border-border bg-secondary/20 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium">
          Edit rule type '{form.code}'
        </h3>
        <button type="button" onClick={onCancel}
                className="text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Code</span>
          <Input
            value={form.code}
            disabled
            className="h-8 text-xs font-mono"
          />
          <span className="text-[10px] text-muted-foreground">
            Code is immutable.
          </span>
        </label>

        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Label</span>
          <Input
            value={form.label}
            onChange={(e) => set("label", e.target.value)}
            placeholder="e.g. High-High, Frozen"
            className="h-8 text-xs"
            maxLength={100}
          />
        </label>

        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground"
                title="Lower rank = more prominent">
            Rank
          </span>
          <Input
            value={form.rank}
            onChange={(e) => set("rank", e.target.value)}
            placeholder="7"
            inputMode="numeric"
            className="h-8 text-xs"
          />
          <span className="text-[10px] text-muted-foreground">
            Must be unique.
          </span>
        </label>

        <label className="flex flex-col gap-1 text-xs col-span-full">
          <span className="text-muted-foreground">Description (optional)</span>
          <textarea
            value={form.description}
            onChange={(e) => set("description", e.target.value)}
            placeholder="What this rule type detects, when an operator should use it..."
            maxLength={2000}
            rows={3}
            className="text-xs bg-card border border-border rounded px-2 py-1.5 font-sans resize-y"
          />
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
          Save changes
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error formatter
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
