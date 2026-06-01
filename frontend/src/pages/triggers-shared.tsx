/**
 * triggers-shared.tsx — shared trigger types, humanizer, validator, and the
 * trigger builder modal. Used by the standalone Report Triggers library page
 * AND the per-report Triggers tab, so both stay consistent.
 */
import { useMemo, useState, type ReactNode } from "react";
import { Clock, Zap, X, Plus, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export type Trigger = {
  id: number; name: string; period: string | null; trigger_type: string;
  owner_report_id: number | null; tag_id: number | null; tag_edge: string | null;
  tag_op: string | null; tag_value: number | null; tag_expr: string | null;
  interval_minutes: number | null; day_of_week: number | null;
  days_of_week: string | null; cron_expr: string | null;
  at_time_min: number | null; at_minute: number | null;
  day_of_month: number | null; month_of_year: number | null;
}

export type TagLite = { id: number; name: string };

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-[11px] mb-1" style={{ color: "var(--ios-gray-1)" }}>{label}</span>
      {children}
    </label>
  );
}
export function Select({ value, options, labels, onChange }: { value: string; options: string[]; labels?: string[]; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-lg px-3 h-9 text-[13px]"
      style={{ border: "0.5px solid var(--card-edge, #ddd)", backgroundColor: "var(--bg, #fff)" }}>
      {options.map((o, i) => <option key={o} value={o}>{labels ? labels[i] : o}</option>)}
    </select>
  );
}

// ===========================================================================
// Humanize a trigger spec into plain English (DanPac-style confirmation).
export const DOW_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const DOW_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function minToHHMM(min: number): string {
  const h = Math.floor(min / 60), m = min % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

// Humanized string for a trigger (used in the modal preview AND the list).
export function humanizeTrigger(t: {
  trigger_type: string; period?: string | null; at_minute?: number | null;
  at_time_min?: number | null; days_of_week?: string | null; day_of_week?: number | null;
  day_of_month?: number | null; month_of_year?: number | null;
  interval_minutes?: number | null; cron_expr?: string | null;
  tag_id?: number | null; tag_edge?: string | null;
  tag_op?: string | null; tag_value?: number | null; tag_expr?: string | null;
}, tagName?: (id: number) => string): string {
  if (t.trigger_type === "tag") {
    const nm = t.tag_id != null ? (tagName?.(t.tag_id) ?? `tag ${t.tag_id}`) : "a tag";
    if (t.tag_expr) return `When ${t.tag_expr.replace(/\bx\b/g, nm)}`;
    if (t.tag_op && t.tag_value != null) return `When ${nm} ${t.tag_op} ${t.tag_value}`;
    const edge = t.tag_edge === "rising" ? "rises" : t.tag_edge === "any_change" ? "changes" : "becomes non-zero";
    return `When ${nm} ${edge}`;
  }
  const time = t.at_time_min != null ? minToHHMM(t.at_time_min) : "06:00";
  const p = (t.period || "").toLowerCase();
  if (t.interval_minutes) return `Every ${t.interval_minutes} minute${t.interval_minutes === 1 ? "" : "s"}`;
  if (p === "hourly" || (t.at_minute != null && !t.at_time_min)) {
    const mm = t.at_minute ?? 0;
    return `Every hour at :${String(mm).padStart(2, "0")}`;
  }
  // weekly: prefer days_of_week
  const days = (t.days_of_week || "").split(",").map((x) => parseInt(x, 10)).filter((n) => n >= 0 && n <= 6);
  if (days.length) {
    const names = days.map((d) => DOW_SHORT[d]).join(", ");
    return `Every ${names} at ${time}`;
  }
  if (t.day_of_week != null) return `Every ${DOW_FULL[t.day_of_week]} at ${time}`;
  if (t.month_of_year) return `Yearly on ${t.month_of_year}/${t.day_of_month ?? 1} at ${time}`;
  if (p === "monthly") return `Monthly on the 1st at ${time}`;
  if (t.day_of_month) return `Monthly on day ${t.day_of_month} at ${time}`;
  if (p === "daily" || t.at_time_min != null) return `Every day at ${time}`;
  if (t.cron_expr) return `Cron: ${t.cron_expr}`;
  return "Unscheduled";
}

// Best-effort cron equivalent (READ-ONLY advanced detail; engine uses fields).
function cronOf(t: {
  period?: string; at_minute?: number; at_time_min?: number; days_of_week?: number[];
  day_of_month?: number; month_of_year?: number; interval_minutes?: number; cron?: string;
}): string {
  if (t.cron) return t.cron;
  const time = t.at_time_min ?? 360;
  const mm = time % 60, hh = Math.floor(time / 60);
  if (t.interval_minutes) return `*/${t.interval_minutes} * * * *`;
  if (t.period === "hourly") return `${t.at_minute ?? 0} * * * *`;
  if (t.period === "weekly" && t.days_of_week?.length) return `${mm} ${hh} * * ${t.days_of_week.join(",")}`;
  if (t.period === "monthly") return `${mm} ${hh} 1 * *`;
  if (t.period === "yearly") return `${mm} ${hh} ${t.day_of_month ?? 1} ${t.month_of_year ?? 1} *`;
  if (t.period === "daily") return `${mm} ${hh} * * *`;
  return "—";
}

// NewTriggerModal — create a custom trigger (timed or tag), DanPac-style.
export function NewTriggerModal({
  reportId, onClose, onCreated, onError, allTags, editing,
}: {
  reportId: number;
  onClose: () => void;
  onCreated: (triggerId: number) => void;
  onError: (m: string) => void;
  allTags: TagLite[];
  editing?: Trigger | null;   // when set, the modal edits (PATCH) instead of creating
}) {
  const isEdit = !!editing;
  const [kind, setKind] = useState<"timed" | "tag">((editing?.trigger_type as any) || "timed");
  const [name, setName] = useState(editing?.name || "");
  const [saving, setSaving] = useState(false);
  // scope: global (reusable across reports) vs custom (private to this report).
  // when editing, keep the trigger's existing scope; new triggers default to GLOBAL.
  const allowCustom = !!reportId; // standalone library (reportId 0) -> global only
  const [scope, setScope] = useState<"global" | "custom">(
    editing ? (editing.owner_report_id != null ? "custom" : "global") : "global");

  type Mode = "hourly" | "daily" | "weekly" | "monthly" | "yearly" | "every_n_minutes" | "cron";
  const initMode: Mode = (() => {
    if (!editing) return "daily";
    if (editing.cron_expr) return "cron";
    if (editing.interval_minutes) return "every_n_minutes";
    if (editing.month_of_year) return "yearly";
    if (editing.period === "monthly" || editing.day_of_month) return "monthly";
    if (editing.days_of_week || editing.day_of_week != null) return "weekly";
    if (editing.period === "hourly") return "hourly";
    return "daily";
  })();
  const minToHHMMlocal = (m: number | null | undefined) => {
    if (m == null) return "06:00";
    return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  };
  const [mode, setMode] = useState<Mode>(initMode);
  const [atMinute, setAtMinute] = useState(editing?.at_minute ?? 0);
  const [timeHHMM, setTimeHHMM] = useState(minToHHMMlocal(editing?.at_time_min));
  const [days, setDays] = useState<number[]>(
    editing?.days_of_week ? editing.days_of_week.split(",").map((x) => parseInt(x, 10)).filter((n) => n >= 0 && n <= 6)
      : (editing?.day_of_week != null ? [editing.day_of_week] : [0]));
  const [moy, setMoy] = useState(editing?.month_of_year ?? 1);
  const [yearDom, setYearDom] = useState(editing?.day_of_month ?? 1);
  const [intervalMin, setIntervalMin] = useState(editing?.interval_minutes ?? 15);
  const [cron, setCron] = useState(editing?.cron_expr || "");
  const [showCron, setShowCron] = useState(false);

  const [tagId, setTagId] = useState<number | null>(editing?.tag_id ?? null);
  const [tagFilter, setTagFilter] = useState("");
  // tag condition mode: edge | compare | formula
  const initialTagMode: "edge" | "compare" | "formula" =
    editing?.tag_expr ? "formula" : (editing?.tag_op ? "compare" : "edge");
  const [tagMode, setTagMode] = useState<"edge" | "compare" | "formula">(initialTagMode);
  const [edge, setEdge] = useState<"to_nonzero" | "rising" | "any_change">(
    (editing?.tag_edge as any) || "to_nonzero");
  const [cmpOp, setCmpOp] = useState<string>(editing?.tag_op || ">");
  const [cmpVal, setCmpVal] = useState<string>(editing?.tag_value != null ? String(editing.tag_value) : "0");
  const [tagExpr, setTagExpr] = useState<string>(editing?.tag_expr || "x > 0");

  const hhmmToMin = (s: string) => {
    const [h, m] = s.split(":").map((x) => parseInt(x, 10));
    return (isNaN(h) ? 0 : h) * 60 + (isNaN(m) ? 0 : m);
  };
  const atTimeMin = hhmmToMin(timeHHMM);

  const filteredTags = allTags.filter(
    (t) => !tagFilter || t.name.toLowerCase().includes(tagFilter.toLowerCase()),
  ).slice(0, 50);

  const toggleDay = (d: number) =>
    setDays((s) => (s.includes(d) ? s.filter((x) => x !== d) : [...s, d].sort((a, b) => a - b)));

  // build the body sent to POST /triggers (concrete fields are the source of truth)
  const buildBody = (): Record<string, unknown> => {
    const base: Record<string, unknown> = {
      name: name.trim() || preview, trigger_type: kind,
      owner_report_id: allowCustom && scope === "custom" ? reportId : null, enabled: true,
    };
    if (kind === "tag") {
      if (tagMode === "formula") return { ...base, tag_id: tagId, tag_expr: tagExpr, tag_op: null, tag_value: null, tag_edge: null };
      if (tagMode === "compare") return { ...base, tag_id: tagId, tag_op: cmpOp, tag_value: parseFloat(cmpVal), tag_expr: null, tag_edge: null };
      return { ...base, tag_id: tagId, tag_edge: edge, tag_op: null, tag_value: null, tag_expr: null };
    }
    switch (mode) {
      case "hourly":          return { ...base, period: "hourly", at_minute: atMinute };
      case "daily":           return { ...base, period: "daily", at_time_min: atTimeMin };
      case "weekly":          return { ...base, period: "weekly", days_of_week: days.join(","), at_time_min: atTimeMin };
      case "monthly":         return { ...base, period: "monthly", day_of_month: 1, at_time_min: atTimeMin }; // fires on the 1st
      case "yearly":          return { ...base, period: "yearly", month_of_year: moy, day_of_month: yearDom, at_time_min: atTimeMin };
      case "every_n_minutes": return { ...base, period: "every_n_minutes", interval_minutes: intervalMin };
      case "cron":            return { ...base, period: "cron", cron_expr: cron };
    }
    return base;
  };

  // live humanized preview
  const preview = useMemo(() => humanizeTrigger({
    trigger_type: kind,
    period: kind === "timed" ? mode : null,
    at_minute: atMinute, at_time_min: atTimeMin,
    days_of_week: days.join(","),
    day_of_month: mode === "monthly" ? 1 : (mode === "yearly" ? yearDom : null),
    month_of_year: mode === "yearly" ? moy : null,
    interval_minutes: mode === "every_n_minutes" ? intervalMin : null,
    cron_expr: mode === "cron" ? cron : null,
    tag_id: tagId, tag_edge: edge,
    tag_op: tagMode === "compare" ? cmpOp : null,
    tag_value: tagMode === "compare" ? parseFloat(cmpVal) : null,
    tag_expr: tagMode === "formula" ? tagExpr : null,
  }, (id) => allTags.find((t) => t.id === id)?.name ?? `tag ${id}`),
  [kind, mode, atMinute, atTimeMin, days, yearDom, moy, intervalMin, cron, tagId, edge, tagMode, cmpOp, cmpVal, tagExpr, allTags]);

  const cronPreview = useMemo(() => cronOf({
    period: mode, at_minute: atMinute, at_time_min: atTimeMin,
    days_of_week: days, day_of_month: mode === "yearly" ? yearDom : 1,
    month_of_year: moy, interval_minutes: mode === "every_n_minutes" ? intervalMin : undefined,
    cron: mode === "cron" ? cron : undefined,
  }), [mode, atMinute, atTimeMin, days, yearDom, moy, intervalMin, cron]);

  const submit = async () => {
    if (kind === "tag" && !tagId) { onError("Pick a tag for a tag trigger."); return; }
    if (kind === "tag" && tagMode === "formula") {
      const fv = validateFormula(tagExpr);
      if (!fv.ok) { onError(`Formula: ${fv.message}`); return; }
    }
    if (kind === "timed" && mode === "weekly" && days.length === 0) { onError("Pick at least one weekday."); return; }
    if (kind === "timed" && mode === "cron" && !cron.trim()) { onError("Enter a cron expression."); return; }
    setSaving(true);
    try {
      if (isEdit && editing) {
        await api.patch(`/report-config/triggers/${editing.id}`, buildBody());
        onCreated(editing.id);
      } else {
        const created = await api.post<Trigger>("/report-config/triggers", buildBody());
        onCreated(created.id);
      }
    } catch (e: any) {
      onError(e?.detail || (isEdit ? "Update trigger failed." : "Create trigger failed."));
    } finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.35)" }} onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl p-5"
        style={{ backgroundColor: "var(--bg-elevated,#fff)", boxShadow: "var(--card-shadow)" }}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-[16px] font-semibold">{isEdit ? "Edit trigger" : "New trigger"}</h2>
          <button onClick={onClose}><X className="h-4 w-4" /></button>
        </div>

        {/* type toggle (DanPac: Timed Trigger / OPC Tag Trigger) */}
        <div className="flex gap-2 mb-3">
          <ToggleBtn active={kind === "timed"} onClick={() => setKind("timed")} icon={<Clock className="h-3.5 w-3.5" />} label="Timed" />
          <ToggleBtn active={kind === "tag"} onClick={() => setKind("tag")} icon={<Zap className="h-3.5 w-3.5" />} label="On tag change" />
        </div>

        {kind === "timed" ? (
          <div className="flex flex-col gap-3">
            <Field label="Schedule">
              <Select value={mode} options={["hourly", "daily", "weekly", "monthly", "yearly", "every_n_minutes", "cron"]}
                labels={["Hourly", "Daily", "Weekly", "Monthly", "Yearly", "Every N minutes", "Cron (advanced)"]}
                onChange={(v) => setMode(v as Mode)} />
            </Field>

            {mode === "hourly" && (
              <Field label="At minute">
                <Select value={String(atMinute)} options={["0", "15", "30", "45"]}
                  labels={[":00", ":15", ":30", ":45"]} onChange={(v) => setAtMinute(+v)} />
              </Field>
            )}
            {(mode === "daily" || mode === "weekly" || mode === "yearly") && (
              <Field label="Time (local)">
                <Input type="time" value={timeHHMM} onChange={(e) => setTimeHHMM(e.target.value)} />
              </Field>
            )}
            {mode === "weekly" && (
              <Field label="Days">
                <div className="flex gap-1">
                  {DOW_SHORT.map((d, i) => (
                    <button key={d} onClick={() => toggleDay(i)}
                      className="flex-1 rounded-md py-1.5 text-[12px] font-medium transition-colors"
                      style={{
                        backgroundColor: days.includes(i) ? "var(--ios-blue,#007aff)" : "var(--bg,#f2f2f7)",
                        color: days.includes(i) ? "#fff" : "var(--text-primary,#1c2530)",
                      }}>{d}</button>
                  ))}
                </div>
              </Field>
            )}
            {mode === "monthly" && (
              <p className="text-[12px] rounded-lg px-3 py-2" style={{ backgroundColor: "var(--bg,#f2f2f7)", color: "var(--ios-gray-1)" }}>
                Fires on the <b>1st of every month</b> at the time below.
              </p>
            )}
            {mode === "monthly" && (
              <Field label="Time (local)">
                <Input type="time" value={timeHHMM} onChange={(e) => setTimeHHMM(e.target.value)} />
              </Field>
            )}
            {mode === "yearly" && (
              <div className="grid grid-cols-2 gap-3">
                <Field label="Month (1-12)">
                  <Input type="number" value={moy} min={1} max={12} onChange={(e) => setMoy(clamp(+e.target.value, 1, 12))} />
                </Field>
                <Field label="Day (1-28)">
                  <Input type="number" value={yearDom} min={1} max={28} onChange={(e) => setYearDom(clamp(+e.target.value, 1, 28))} />
                </Field>
              </div>
            )}
            {mode === "every_n_minutes" && (
              <Field label="Interval (minutes)">
                <Select value={String(intervalMin)} options={["5", "10", "15", "30", "60"]}
                  labels={["5 min", "10 min", "15 min", "30 min", "60 min"]} onChange={(v) => setIntervalMin(+v)} />
              </Field>
            )}
            {mode === "cron" && (
              <Field label="Cron expression">
                <Input value={cron} placeholder="*/15 * * * *" onChange={(e) => setCron(e.target.value)} />
              </Field>
            )}
            {mode === "cron" && (
              <p className="text-[11px]" style={{ color: "var(--ios-orange,#9a5800)" }}>
                Cron only fires if the server has the optional croniter package. All other schedules work without it.
              </p>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <Field label="Tag">
              {tagId != null ? (
                <div className="flex items-center gap-2">
                  <span className="rounded-full px-2 py-1 text-[12px]"
                    style={{ backgroundColor: "var(--ios-blue-soft,#e6f0fe)", color: "var(--ios-blue,#0040a0)" }}>
                    {allTags.find((t) => t.id === tagId)?.name ?? `tag ${tagId}`}
                  </span>
                  <button onClick={() => setTagId(null)}><X className="h-3.5 w-3.5" /></button>
                </div>
              ) : (
                <>
                  <Input placeholder="Search tags…" value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} />
                  {tagFilter && (
                    <div className="mt-1 max-h-36 overflow-auto rounded-lg" style={{ border: "0.5px solid var(--card-edge,#ddd)" }}>
                      {filteredTags.map((t) => (
                        <button key={t.id} onClick={() => { setTagId(t.id); setTagFilter(""); }}
                          className="block w-full text-left px-3 py-1.5 text-[12.5px] hover:bg-[var(--ios-blue-soft,#e6f0fe)]">
                          {t.name}
                        </button>
                      ))}
                      {filteredTags.length === 0 && <div className="px-3 py-2 text-[12px]" style={{ color: "var(--ios-gray-1)" }}>No matches.</div>}
                    </div>
                  )}
                </>
              )}
            </Field>
            <Field label="Condition type">
              <div className="flex gap-1">
                {([["edge","Edge"],["compare","Compare"],["formula","Formula"]] as const).map(([m, lbl]) => (
                  <button key={m} onClick={() => setTagMode(m)}
                    className="flex-1 rounded-md py-1.5 text-[12px] font-medium transition-colors"
                    style={{
                      backgroundColor: tagMode === m ? "var(--ios-blue,#007aff)" : "var(--bg,#f2f2f7)",
                      color: tagMode === m ? "#fff" : "var(--text-primary,#1c2530)",
                    }}>{lbl}</button>
                ))}
              </div>
            </Field>
            {tagMode === "edge" && (
              <Field label="Fire when value">
                <Select value={edge} options={["to_nonzero", "rising", "any_change"]}
                  labels={["becomes non-zero", "rises", "changes (any)"]} onChange={(v) => setEdge(v as typeof edge)} />
              </Field>
            )}
            {tagMode === "compare" && (
              <Field label="Fire when value crosses">
                <div className="flex gap-2 items-center">
                  <span className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>Tag</span>
                  <Select value={cmpOp} options={["=", "!=", ">", ">=", "<", "<="]} onChange={setCmpOp} />
                  <Input type="number" value={cmpVal} onChange={(e) => setCmpVal(e.target.value)} className="w-24" />
                </div>
              </Field>
            )}
            {tagMode === "formula" && (
              <Field label="Formula (x = tag value)">
                <Input value={tagExpr} placeholder="x > 50 and x < 90" onChange={(e) => setTagExpr(e.target.value)} />
                {(() => {
                  const r = validateFormula(tagExpr);
                  return (
                    <div className="mt-1 flex items-center gap-1.5 text-[11.5px]"
                      style={{ color: r.ok ? "var(--ios-green,#1a7a3e)" : "var(--ios-red,#c0392b)" }}>
                      {r.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
                      {r.message}
                    </div>
                  );
                })()}
                <div className="mt-1.5 text-[11px] leading-relaxed" style={{ color: "var(--ios-gray-1)" }}>
                  Supported: <b>x</b> (the tag value), numbers, <code>and or not</code>,
                  comparisons <code>== != &lt; &lt;= &gt; &gt;=</code>, arithmetic <code>+ - * / % **</code>, parentheses.
                  Use <code>==</code> not <code>=</code>; no lists or <code>in</code> (use <code>or</code>).
                  Examples: <code>x == 4</code> · <code>x &gt; 50 and x &lt; 90</code> · <code>x &lt; 10 or x &gt; 90</code> · <code>not (x == 0)</code>
                </div>
              </Field>
            )}
            <p className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
              {tagMode === "edge" && "Fires on a transition (e.g. value leaves 0)."}
              {tagMode === "compare" && "Fires once each time the comparison becomes true (not every reading while true)."}
              {tagMode === "formula" && "Safe expression; x is the tag's value. Fires when it becomes true. e.g. x == 4, x > 50 and x < 90."}
            </p>
          </div>
        )}

        {/* Humanized confirmation (DanPac summary line) */}
        <div className="mt-3 rounded-lg px-3 py-2 text-[13px] font-medium"
          style={{ backgroundColor: "var(--ios-green-soft,#e5f8eb)", color: "var(--ios-green,#1a7a3e)" }}>
          {preview}
        </div>

        {/* cron equivalent — read-only advanced detail */}
        {kind === "timed" && mode !== "cron" && (
          <button onClick={() => setShowCron((s) => !s)} className="mt-1 text-[11px] underline"
            style={{ color: "var(--ios-gray-1)" }}>
            {showCron ? "Hide" : "Show"} cron equivalent
          </button>
        )}
        {kind === "timed" && mode !== "cron" && showCron && (
          <div className="mt-1 font-mono text-[12px] rounded px-2 py-1"
            style={{ backgroundColor: "var(--bg,#f2f2f7)", color: "var(--text-primary,#1c2530)" }}>
            {cronPreview} <span style={{ color: "var(--ios-gray-1)" }}>(informational; engine uses the fields above)</span>
          </div>
        )}

        {allowCustom ? (
          <Field label="Availability">
            <div className="flex gap-1">
              {([["global","Global (reusable)"],["custom","This report only"]] as const).map(([s, lbl]) => (
                <button key={s} onClick={() => setScope(s)}
                  className="flex-1 rounded-md py-1.5 text-[12px] font-medium transition-colors"
                  style={{
                    backgroundColor: scope === s ? "var(--ios-blue,#007aff)" : "var(--bg,#f2f2f7)",
                    color: scope === s ? "#fff" : "var(--text-primary,#1c2530)",
                  }}>{lbl}</button>
              ))}
            </div>
            <p className="mt-1 text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
              {scope === "global"
                ? "Global triggers appear in every report's trigger list and can be reused. Edit once, applies everywhere."
                : "Custom triggers belong to this report only and aren't offered to others."}
            </p>
          </Field>
        ) : (
          <p className="text-[11px] rounded-lg px-3 py-2" style={{ backgroundColor: "var(--ios-blue-soft,#e6f0fe)", color: "var(--ios-blue,#0040a0)" }}>
            Global trigger — available to all reports.
          </p>
        )}

        <Field label="Name (optional — auto-named if blank)">
          <Input value={name} placeholder={preview} onChange={(e) => setName(e.target.value)} />
        </Field>

        <div className="flex justify-end gap-2 mt-4">
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={submit} disabled={saving}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
            <span className="ml-1">{isEdit ? "Save changes" : "Create & link"}</span>
          </Button>
        </div>
      </div>
    </div>
  );
}

function ToggleBtn({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: ReactNode; label: string }) {
  return (
    <button onClick={onClick}
      className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors"
      style={{
        backgroundColor: active ? "var(--ios-blue,#007aff)" : "var(--bg,#f2f2f7)",
        color: active ? "#fff" : "var(--text-primary,#1c2530)",
      }}>
      {icon}{label}
    </button>
  );
}

// Validate a tag formula in the browser, mirroring the backend simpleeval rules
// (boolean and/or/not + comparisons + arithmetic; NO lists/tuples, `in`, calls,
// attributes, strings, or names other than x/value/v). Keeps the green check
// honest about what will actually fire server-side.
function validateFormula(expr: string): { ok: boolean; message: string } {
  const e = (expr || "").trim();
  if (!e) return { ok: false, message: "Empty formula." };
  if (/[\[\]]/.test(e)) return { ok: false, message: "Lists [] aren't supported. Use 'or' (e.g. x==1 or x==2)." };
  if (/\bin\b/.test(e)) return { ok: false, message: "'in' / membership isn't supported. Use 'or' (e.g. x==1 or x==2)." };
  if (/[a-zA-Z_][a-zA-Z0-9_]*\s*\(/.test(e.replace(/\b(and|or|not)\b/g, " ")))
    return { ok: false, message: "Function calls aren't allowed." };
  if (/['"]/.test(e)) return { ok: false, message: "Text/strings aren't allowed - numeric conditions only." };
  if (/[a-zA-Z_]\./.test(e)) return { ok: false, message: "Attribute access ('.') isn't allowed." };
  if (/=(?![=])/.test(e.replace(/[<>!=]=/g, ""))) return { ok: false, message: "Use '==' for equality, not '='." };
  const idents = e.match(/[a-zA-Z_][a-zA-Z0-9_]*/g) || [];
  const allowed = new Set(["x", "value", "v", "and", "or", "not", "True", "False"]);
  for (const id of idents) {
    if (!allowed.has(id)) return { ok: false, message: `Unknown name '${id}'. Use x (the tag value), numbers, and/or/not.` };
  }
  if (!/\b(x|value|v)\b/.test(e)) return { ok: false, message: "Formula must reference x (the tag value)." };
  if (!/(==|!=|<=|>=|<|>)/.test(e)) return { ok: false, message: "Add a comparison (e.g. x > 0)." };
  let depth = 0;
  for (const c of e) { if (c === "(") depth++; if (c === ")") depth--; if (depth < 0) return { ok: false, message: "Unbalanced parentheses." }; }
  if (depth !== 0) return { ok: false, message: "Unbalanced parentheses." };
  try {
    const js = e.replace(/\band\b/g, "&&").replace(/\bor\b/g, "||").replace(/\bnot\b/g, "!")
      .replace(/\bTrue\b/g, "true").replace(/\bFalse\b/g, "false")
      .replace(/(=)(?![=])/g, "==");
    const chained = /([<>]=?|==|!=)\s*[^<>=!()]+\s*([<>]=?)/.test(js);
    if (!chained) {
      const fn = new Function("x", "value", "v", `return (${js});`);
      if (typeof fn(42, 42, 42) !== "boolean") return { ok: false, message: "Formula must evaluate to true/false." };
    }
  } catch {
    return { ok: false, message: "Couldn't parse the formula - check the syntax." };
  }
  return { ok: true, message: "Valid condition." };
}

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, isNaN(n) ? lo : n));
}
