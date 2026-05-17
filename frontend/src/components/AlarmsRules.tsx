/**
 * Phase 14.5 — Alarm rules CRUD.
 *
 * Lists every configured rule with per-row actions (toggle enabled,
 * edit, delete). An inline form expands above the list for create/edit.
 * The form covers the four "evaluable" rule types (hi_hi / hi / lo /
 * lo_lo). deviation and rate_of_change are accepted by the backend
 * schema but not yet executed by the evaluator, so we don't expose
 * them here.
 */
import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Pencil, Trash2, Power, X, Save, AlertTriangle, RefreshCw,
} from "lucide-react";

import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { SeverityBadge } from "@/components/SeverityBadge";

import type {
  AlarmRule, AlarmRuleCreate, AlarmRuleUpdate, RuleType, Severity,
} from "@/types/alarms";
import {
  EVALUABLE_RULE_TYPES, RULE_TYPE_LABELS,
} from "@/types/alarms";
import { useSeverities } from "@/lib/useSeverities";
import { useRuleTypes } from "@/lib/useRuleTypes";
import type { AlarmRuleType } from "@/types/alarmRuleTypes";

type TagOption = { id: number; name: string };

// What the form holds while the operator is editing. tag_id can be 0
// for "no selection yet" since 0 isn't a valid tag id.
interface FormState {
  id: number | null;         // null when creating, number when editing
  tag_id: number;
  rule_type: RuleType;
  severity: Severity;
  threshold: string;         // strings so empty inputs are representable
  deadband: string;
  on_delay_sec: string;
  off_delay_sec: string;
  latched: boolean;
  enabled: boolean;
  message_template: string;
  // Phase 14.7 — rolling window for deviation / rate_of_change.
  // Shown only when one of those rule types is selected.
  window_seconds: string;
}

const EMPTY_FORM: FormState = {
  id: null,
  tag_id: 0,
  rule_type: "hi",
  severity: "high",
  threshold: "",
  deadband: "0",
  on_delay_sec: "0",
  off_delay_sec: "0",
  latched: false,
  enabled: true,
  message_template: "",
  window_seconds: "60",
};

export default function AlarmsRules() {
  const qc = useQueryClient();
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  // ---- queries ----

  const rulesQuery = useQuery({
    queryKey: ["alarms-rules"],
    queryFn: () => api.get<AlarmRule[]>("/alarms/rules"),
    staleTime: 5_000,
  });

  // Phase 14.6 — load severities from the master table instead of using
  // the hardcoded ALL_SEVERITIES constant. Operators can now add custom
  // severities under Setup > Alarm Severities and they appear here.
  // Phase 14.8 — the per-row badge label is rendered by <SeverityBadge>
  // which calls useSeverities itself; this hook stays for the form
  // dropdown options only.
  const severitiesQuery = useSeverities();
  const severityOptions = useMemo(
    () => severitiesQuery.data ?? [],
    [severitiesQuery.data]
  );

  // Phase 14.6b — load rule types from the master table. Includes
  // is_evaluable flag so the dropdown can warn when picking inert
  // types (operator can save the rule but it'll never fire).
  const ruleTypesQuery = useRuleTypes();
  const ruleTypeOptions = useMemo(
    () => ruleTypesQuery.data ?? [],
    [ruleTypesQuery.data]
  );
  const ruleTypeLabel = (code: string): string =>
    ruleTypeOptions.find((rt) => rt.code === code)?.label ??
    RULE_TYPE_LABELS[code as RuleType] ?? code;
  const ruleTypeIsEvaluable = (code: string): boolean =>
    ruleTypeOptions.find((rt) => rt.code === code)?.is_evaluable ??
    EVALUABLE_RULE_TYPES.includes(code as RuleType);

  // Tag list for the picker. Tags rarely change so a fairly long
  // staleTime is fine. If the list is huge in a customer install,
  // we'd swap this for a search-as-you-type combobox.
  const tagsQuery = useQuery({
    queryKey: ["tags-all"],
    queryFn: () => api.get<TagOption[]>("/tags"),
    staleTime: 60_000,
  });

  const tagOptions: TagOption[] = useMemo(() => {
    const raw = tagsQuery.data ?? [];
    return [...raw].sort((a, b) => a.name.localeCompare(b.name));
  }, [tagsQuery.data]);

  const tagNameById = useMemo(() => {
    const m = new Map<number, string>();
    for (const t of tagOptions) m.set(t.id, t.name);
    return m;
  }, [tagOptions]);

  // ---- mutations ----

  const createMutation = useMutation({
    mutationFn: (payload: AlarmRuleCreate) =>
      api.post<AlarmRule>("/alarms/rules", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alarms-rules"] });
      qc.invalidateQueries({ queryKey: ["alarms-active"] });
      closeForm();
    },
    onError: (e: unknown) => setFormError(errorText(e)),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: AlarmRuleUpdate }) =>
      api.patch<AlarmRule>(`/alarms/rules/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alarms-rules"] });
      qc.invalidateQueries({ queryKey: ["alarms-active"] });
      closeForm();
    },
    onError: (e: unknown) => setFormError(errorText(e)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/alarms/rules/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alarms-rules"] });
      qc.invalidateQueries({ queryKey: ["alarms-active"] });
    },
  });

  const toggleEnabled = (rule: AlarmRule) => {
    updateMutation.mutate({ id: rule.id, body: { enabled: !rule.enabled } });
  };

  // ---- form helpers ----

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (rule: AlarmRule) => {
    setForm({
      id: rule.id,
      tag_id: rule.tag_id,
      rule_type: rule.rule_type,
      severity: rule.severity,
      threshold: String(rule.threshold),
      deadband: String(rule.deadband),
      on_delay_sec: String(rule.on_delay_sec),
      off_delay_sec: String(rule.off_delay_sec),
      latched: rule.latched,
      enabled: rule.enabled,
      message_template: rule.message_template ?? "",
      window_seconds: rule.window_seconds != null ? String(rule.window_seconds) : "60",
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

    // Light client-side validation before round-tripping to the API.
    if (!form.tag_id) return setFormError("Pick a tag.");

    // Phase 14.9 — Boolean rule types don't use threshold or deadband.
    // Form hides those inputs entirely; we default-fill them so the
    // payload still satisfies the API's typed schema.
    const isBool = form.rule_type === "bool_true" || form.rule_type === "bool_false";

    let threshold: number;
    let deadband: number;
    if (isBool) {
      threshold = 0;
      deadband = 0;
    } else {
      threshold = Number(form.threshold);
      if (!isFinite(threshold)) return setFormError("Threshold must be a number.");
      deadband = Number(form.deadband);
      if (!isFinite(deadband) || deadband < 0) return setFormError("Deadband must be >= 0.");
    }

    const onDelay = parseInt(form.on_delay_sec, 10);
    if (!isFinite(onDelay) || onDelay < 0) return setFormError("On-delay must be an integer >= 0.");
    const offDelay = parseInt(form.off_delay_sec, 10);
    if (!isFinite(offDelay) || offDelay < 0) return setFormError("Off-delay must be an integer >= 0.");

    // Phase 14.7 — window_seconds only matters for deviation / rate_of_change.
    // For other rule types we send null (the API ignores it).
    const isWindowed = form.rule_type === "deviation" || form.rule_type === "rate_of_change";
    let windowSeconds: number | null = null;
    if (isWindowed) {
      const ws = parseInt(form.window_seconds, 10);
      if (!isFinite(ws) || ws < 1 || ws > 86400) {
        return setFormError("Window must be an integer between 1 and 86400 seconds.");
      }
      windowSeconds = ws;
    }

    const body: AlarmRuleCreate = {
      tag_id: form.tag_id,
      rule_type: form.rule_type,
      severity: form.severity,
      threshold,
      deadband,
      on_delay_sec: onDelay,
      off_delay_sec: offDelay,
      latched: form.latched,
      enabled: form.enabled,
      message_template: form.message_template.trim() || null,
      window_seconds: windowSeconds,
    };

    if (form.id == null) {
      createMutation.mutate(body);
    } else {
      // PATCH excludes tag_id (it's immutable by API design)
      const { tag_id: _omit, ...patchBody } = body;
      updateMutation.mutate({ id: form.id, body: patchBody });
    }
  };

  const handleDelete = (rule: AlarmRule) => {
    const label = rule.tag_name ?? `tag #${rule.tag_id}`;
    if (!confirm(
      `Delete the ${RULE_TYPE_LABELS[rule.rule_type]} rule on ${label}?\n` +
      `The event history for this rule will be preserved.`
    )) return;
    deleteMutation.mutate(rule.id);
  };

  // ---- render ----

  const rules = rulesQuery.data ?? [];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center justify-between gap-3 flex-wrap">
          <span className="flex items-center gap-2">
            Alarm rules
            <span className="text-xs text-muted-foreground font-normal">
              {rules.length} configured
            </span>
            {(rulesQuery.isFetching || updateMutation.isPending || deleteMutation.isPending) && (
              <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />
            )}
          </span>
          {!formOpen && (
            <Button size="sm" variant="outline" className="h-7 text-xs gap-1"
                    onClick={openCreate}>
              <Plus className="h-3 w-3" />
              New rule
            </Button>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {formOpen && (
          <RuleForm
            form={form}
            onChange={setForm}
            error={formError}
            saving={createMutation.isPending || updateMutation.isPending}
            tagOptions={tagOptions}
            severityOptions={severityOptions}
            ruleTypeOptions={ruleTypeOptions}
            onSubmit={handleSubmit}
            onCancel={closeForm}
            tagsLoading={tagsQuery.isLoading}
          />
        )}

        {rulesQuery.isLoading && (
          <p className="text-xs text-muted-foreground py-6 px-3">Loading rules…</p>
        )}

        {rulesQuery.isError && (
          <div className="flex items-start gap-2 text-xs text-destructive py-3 px-3">
            <AlertTriangle className="h-4 w-4 flex-shrink-0" />
            <span>Failed to load: {(rulesQuery.error as Error)?.message}</span>
          </div>
        )}

        {rules.length === 0 && rulesQuery.data && (
          <p className="text-xs text-muted-foreground py-8 px-3 text-center">
            No alarm rules configured yet. Click <strong>New rule</strong> to add one.
          </p>
        )}

        {rules.length > 0 && (
          <div className="overflow-x-auto border-t border-border">
            <table className="w-full text-xs">
              <thead className="bg-secondary/40 text-[10px] uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">Tag</th>
                  <th className="text-left px-3 py-2 font-medium">Type</th>
                  <th className="text-left px-3 py-2 font-medium">Severity</th>
                  <th className="text-right px-3 py-2 font-medium">Threshold</th>
                  <th className="text-right px-3 py-2 font-medium">Deadband</th>
                  <th className="text-right px-3 py-2 font-medium" title="on_delay / off_delay seconds">Delays</th>
                  <th className="text-center px-3 py-2 font-medium">Latched</th>
                  <th className="text-center px-3 py-2 font-medium">Enabled</th>
                  <th className="text-right px-3 py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id} className="border-t border-border hover:bg-secondary/30">
                    <td className="px-3 py-1.5">{r.tag_name ?? `#${r.tag_id}`}</td>
                    <td className="px-3 py-1.5">
                      <Badge
                        variant="outline"
                        className={`text-[10px] font-mono ${
                          ruleTypeIsEvaluable(r.rule_type)
                            ? ""
                            : "bg-amber-50 text-amber-800 border-amber-300"
                        }`}
                        title={
                          ruleTypeIsEvaluable(r.rule_type)
                            ? undefined
                            : "Evaluator has no logic for this rule type — this rule will not fire."
                        }
                      >
                        {ruleTypeLabel(r.rule_type)}
                        {!ruleTypeIsEvaluable(r.rule_type) && " (inert)"}
                      </Badge>
                    </td>
                    <td className="px-3 py-1.5">
                      <SeverityBadge code={r.severity} />
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {(r.rule_type === "bool_true" || r.rule_type === "bool_false")
                        ? <span className="text-muted-foreground">n/a</span>
                        : (r.rule_type === "deviation" || r.rule_type === "rate_of_change")
                          ? (
                            <span title={r.rule_type === "rate_of_change" ? "units/sec over window" : "max deviation from rolling mean"}>
                              {r.threshold}
                              <span className="text-[10px] text-muted-foreground ml-1">
                                @ {r.window_seconds ?? 60}s
                              </span>
                            </span>
                          )
                          : r.threshold}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">
                      {(r.rule_type === "bool_true" || r.rule_type === "bool_false")
                        ? "n/a"
                        : r.deadband}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">
                      {r.on_delay_sec}s / {r.off_delay_sec}s
                    </td>
                    <td className="px-3 py-1.5 text-center text-muted-foreground">
                      {r.latched ? "✓" : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-center">
                      <button
                        type="button"
                        onClick={() => toggleEnabled(r)}
                        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] ${
                          r.enabled
                            ? "bg-emerald-50 text-emerald-800 border border-emerald-300"
                            : "bg-slate-100 text-slate-600 border border-slate-300"
                        }`}
                        title={r.enabled ? "Click to disable" : "Click to enable"}
                      >
                        <Power className="h-2.5 w-2.5" />
                        {r.enabled ? "ON" : "OFF"}
                      </button>
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <div className="inline-flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => openEdit(r)}
                          className="p-1 rounded hover:bg-secondary/60 text-muted-foreground hover:text-foreground"
                          title="Edit"
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(r)}
                          className="p-1 rounded hover:bg-red-100 text-muted-foreground hover:text-red-700"
                          title="Delete"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Inline create/edit form
// ---------------------------------------------------------------------------

function RuleForm({
  form, onChange, error, saving, tagOptions, severityOptions, ruleTypeOptions,
  tagsLoading, onSubmit, onCancel,
}: {
  form: FormState;
  onChange: (f: FormState) => void;
  error: string | null;
  saving: boolean;
  tagOptions: TagOption[];
  severityOptions: { code: string; label: string; rank: number; color_hex: string }[];
  ruleTypeOptions: AlarmRuleType[];
  tagsLoading: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    onChange({ ...form, [k]: v });

  const editing = form.id != null;
  const numericLikeProps = { inputMode: "numeric" as const, className: "h-8 text-xs" };

  return (
    <div className="border-b border-border bg-secondary/20 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium">
          {editing ? `Edit rule #${form.id}` : "New alarm rule"}
        </h3>
        <button type="button" onClick={onCancel}
                className="text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Tag */}
        <label className="flex flex-col gap-1 text-xs col-span-2">
          <span className="text-muted-foreground">Tag</span>
          <select
            value={form.tag_id || ""}
            onChange={(e) => set("tag_id", parseInt(e.target.value, 10) || 0)}
            disabled={editing || tagsLoading}
            className="h-8 text-xs bg-card border border-border rounded px-2 disabled:opacity-60"
          >
            <option value="">{tagsLoading ? "Loading tags…" : "Select a tag…"}</option>
            {tagOptions.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
          {editing && (
            <span className="text-[10px] text-muted-foreground">
              Tag is immutable. Delete + recreate to change it.
            </span>
          )}
        </label>

        {/* Rule type */}
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Type</span>
          <select
            value={form.rule_type}
            onChange={(e) => set("rule_type", e.target.value as RuleType)}
            className="h-8 text-xs bg-card border border-border rounded px-2"
          >
            {ruleTypeOptions.map((t) => (
              <option key={t.code} value={t.code}>
                {t.label}{!t.is_evaluable ? " (no evaluator support)" : ""}
              </option>
            ))}
          </select>
          {(() => {
            const picked = ruleTypeOptions.find((t) => t.code === form.rule_type);
            if (!picked || picked.is_evaluable) return null;
            return (
              <span className="text-[10px] text-amber-700 leading-tight mt-0.5">
                ⚠ The evaluator has no logic for this type. The rule can
                be saved but will <strong>never fire</strong> until
                evaluator support ships.
              </span>
            );
          })()}
        </label>

        {/* Severity */}
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Severity</span>
          <select
            value={form.severity}
            onChange={(e) => set("severity", e.target.value as Severity)}
            className="h-8 text-xs bg-card border border-border rounded px-2"
          >
            {severityOptions.map((s) => (
              <option key={s.code} value={s.code}>{s.label}</option>
            ))}
          </select>
        </label>

        {/* Threshold + Deadband — hidden for boolean rule types since
            the comparison is encoded by the type itself. Phase 14.9. */}
        {(() => {
          const isBool = form.rule_type === "bool_true" || form.rule_type === "bool_false";
          if (isBool) {
            return (
              <div className="col-span-2 text-[11px] text-muted-foreground bg-secondary/30 border border-border rounded px-2 py-1.5">
                <strong>Boolean rule —</strong>{" "}
                {form.rule_type === "bool_true"
                  ? "fires when value ≠ 0 (asserted)."
                  : "fires when value = 0 (absent)."}
                {" "}Threshold / deadband not used.
                The on-delay below acts as a debounce timer.
              </div>
            );
          }
          return (
            <>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Threshold</span>
                <Input value={form.threshold}
                       onChange={(e) => set("threshold", e.target.value)}
                       placeholder="e.g. 100"
                       {...numericLikeProps} />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground"
                      title="Hysteresis around the threshold to prevent chatter">
                  Deadband
                </span>
                <Input value={form.deadband}
                       onChange={(e) => set("deadband", e.target.value)}
                       placeholder="0"
                       {...numericLikeProps} />
              </label>
            </>
          );
        })()}

        {/* Window (seconds) — Phase 14.7. Shown only for deviation
            and rate_of_change rules. Each gets its own threshold-unit
            hint since the meaning of "threshold" differs by type. */}
        {(() => {
          const isWindowed = form.rule_type === "deviation" ||
                             form.rule_type === "rate_of_change";
          if (!isWindowed) return null;
          const hint = form.rule_type === "deviation"
            ? "Rolling mean is computed over this window. Threshold is in tag units (alarm fires when |value - mean| > threshold)."
            : "Slope is fitted over this window. Threshold is in tag-units per second (alarm fires when |slope| > threshold).";
          return (
            <label className="flex flex-col gap-1 text-xs col-span-2">
              <span className="text-muted-foreground" title={hint}>
                Window (seconds)
              </span>
              <Input value={form.window_seconds}
                     onChange={(e) => set("window_seconds", e.target.value)}
                     placeholder="60"
                     {...numericLikeProps} />
              <span className="text-[10px] text-muted-foreground leading-tight">
                {hint}
              </span>
            </label>
          );
        })()}

        {/* On delay */}
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground" title="Seconds the condition must persist before activation">
            On-delay (s)
          </span>
          <Input value={form.on_delay_sec}
                 onChange={(e) => set("on_delay_sec", e.target.value)}
                 placeholder="0"
                 {...numericLikeProps} />
        </label>

        {/* Off delay */}
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground" title="Seconds the clear condition must persist before deactivation">
            Off-delay (s)
          </span>
          <Input value={form.off_delay_sec}
                 onChange={(e) => set("off_delay_sec", e.target.value)}
                 placeholder="0"
                 {...numericLikeProps} />
        </label>

        {/* Latched */}
        <label className="flex items-start gap-2 text-xs mt-4">
          <input type="checkbox"
                 checked={form.latched}
                 onChange={(e) => set("latched", e.target.checked)} />
          <span>
            <strong>Latched</strong>
            <span className="block text-[10px] text-muted-foreground">
              Stays active until acknowledged
            </span>
          </span>
        </label>

        {/* Enabled */}
        <label className="flex items-start gap-2 text-xs mt-4">
          <input type="checkbox"
                 checked={form.enabled}
                 onChange={(e) => set("enabled", e.target.checked)} />
          <span>
            <strong>Enabled</strong>
            <span className="block text-[10px] text-muted-foreground">
              Evaluator processes this rule
            </span>
          </span>
        </label>

        {/* Message template - spans the row */}
        <label className="flex flex-col gap-1 text-xs col-span-full">
          <span className="text-muted-foreground">
            Message template{" "}
            <span className="text-[10px]">
              (supports <code>{"{value}"}</code>, <code>{"{threshold}"}</code>, <code>{"{rule_type}"}</code>)
            </span>
          </span>
          <Input value={form.message_template}
                 onChange={(e) => set("message_template", e.target.value)}
                 placeholder="e.g. Pressure exceeded {threshold} mA (now {value})"
                 className="h-8 text-xs" />
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
          {editing ? "Save changes" : "Create rule"}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function errorText(e: unknown): string {
  if (e instanceof Error) return e.message;
  // FastAPI errors come back with { detail: "..." }
  if (typeof e === "object" && e && "detail" in e) {
    const d = (e as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    return JSON.stringify(d);
  }
  return String(e);
}
