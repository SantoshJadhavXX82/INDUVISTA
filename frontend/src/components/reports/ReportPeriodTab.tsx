/**
 * Period tab — configure a report's DATA WINDOW (period rule), decoupled from
 * its trigger. Backed by GET/PUT/DELETE /report-config/definitions/{id}/period-rule.
 *
 * When enabled, the report aggregates each binding over the computed window
 * (see the Data tab for per-tag functions). When disabled, the report renders
 * a live snapshot — the legacy behavior. Shift uses the plant shift schedule
 * (Settings → Shifts); the batch period type is omitted until batch records
 * exist (the backend cannot resolve it yet).
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Save, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, Select } from "@/pages/triggers-shared";

type PeriodRule = {
  report_id: number;
  period_type: string;
  period_rule: string;
  boundary_offset_min: number;
  custom_start_offset_min: number | null;
  custom_end_offset_min: number | null;
  allow_partial: boolean;
  late_data_wait_sec: number;
  grace_sec: number;
  missing_period_handling: string;
  label_format: string;
  filename_format: string;
  enabled: boolean;
};

const PERIOD_TYPES = ["hourly", "daily", "weekly", "monthly", "shift", "custom"];
const PERIOD_TYPE_LABELS = ["Hourly", "Daily", "Weekly", "Monthly", "Shift", "Custom (offsets)"];
const PERIOD_RULES = ["previous_completed", "current"];
const PERIOD_RULE_LABELS = ["Previous completed period", "Current (in-progress) period"];
const MISSING = ["warn", "hold", "fail"];
const MISSING_LABELS = ["Generate with warning", "Hold (skip)", "Fail"];

function numOr(v: string, fallback: number): number {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : fallback;
}

function describe(pt: string, pr: string, boundary: number, cs: number, ce: number): string {
  if (pt === "custom") {
    return `Each render covers ${cs} to ${ce} minutes relative to the current clock hour.`;
  }
  const rule = pr === "current" ? "the current, in-progress" : "the last completed";
  const unit = { hourly: "hour", daily: "day", weekly: "week", monthly: "month", shift: "shift" }[pt] ?? "period";
  const at = (pt === "daily" || pt === "weekly" || pt === "monthly") && boundary
    ? ` (boundary at +${boundary} min from midnight)`
    : "";
  return `Each render covers ${rule} ${unit}${at}, in plant time.`;
}

export function ReportPeriodTab({
  defId, onSaved, onError,
}: {
  defId: number;
  onSaved: () => void;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const ruleQ = useQuery({
    queryKey: ["report-period-rule", defId],
    queryFn: () => api.get<PeriodRule | null>(`/report-config/definitions/${defId}/period-rule`),
  });

  const [enabled, setEnabled] = useState(false);
  const [periodType, setPeriodType] = useState("hourly");
  const [periodRule, setPeriodRule] = useState("previous_completed");
  const [boundaryMin, setBoundaryMin] = useState(0);
  const [customStart, setCustomStart] = useState(-60);
  const [customEnd, setCustomEnd] = useState(0);
  const [missing, setMissing] = useState("warn");
  const [labelFormat, setLabelFormat] = useState("yyyy-MM-dd HH:mm");
  const [filenameFormat, setFilenameFormat] = useState("yyyyMMdd_HHmm");

  // Hydrate from the saved rule (or fall back to sensible defaults).
  useEffect(() => {
    const r = ruleQ.data;
    if (r) {
      setEnabled(true);
      setPeriodType(r.period_type);
      setPeriodRule(r.period_rule === "custom" ? "previous_completed" : r.period_rule);
      setBoundaryMin(r.boundary_offset_min ?? 0);
      setCustomStart(r.custom_start_offset_min ?? -60);
      setCustomEnd(r.custom_end_offset_min ?? 0);
      setMissing(r.missing_period_handling ?? "warn");
      setLabelFormat(r.label_format ?? "yyyy-MM-dd HH:mm");
      setFilenameFormat(r.filename_format ?? "yyyyMMdd_HHmm");
    } else {
      setEnabled(false);
    }
  }, [ruleQ.data]);

  const isCustom = periodType === "custom";
  const showBoundary = periodType === "daily" || periodType === "weekly" || periodType === "monthly";

  const customInvalid = isCustom && customEnd <= customStart;

  const save = useMutation({
    mutationFn: async () => {
      if (!enabled) {
        // Remove any existing rule -> report reverts to live-snapshot mode.
        await api.delete(`/report-config/definitions/${defId}/period-rule`);
        return;
      }
      await api.put(`/report-config/definitions/${defId}/period-rule`, {
        period_type: periodType,
        period_rule: isCustom ? "custom" : periodRule,
        boundary_offset_min: showBoundary ? boundaryMin : 0,
        custom_start_offset_min: isCustom ? customStart : null,
        custom_end_offset_min: isCustom ? customEnd : null,
        missing_period_handling: missing,
        label_format: labelFormat,
        filename_format: filenameFormat,
        enabled: true,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["report-period-rule", defId] });
      onSaved();
    },
    onError: (e: any) => onError(e?.detail || "Saving the period rule failed."),
  });

  const summary = useMemo(
    () => describe(periodType, periodRule, boundaryMin, customStart, customEnd),
    [periodType, periodRule, boundaryMin, customStart, customEnd],
  );

  return (
    <SectionCard
      title="Period — data window"
      action={
        <Button size="sm" onClick={() => save.mutate()}
          disabled={save.isPending || ruleQ.isLoading || customInvalid}>
          {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : enabled ? <Save className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />}
          <span className="ml-1">{enabled ? "Save period" : "Use live snapshot"}</span>
        </Button>
      }
    >
      <label className="flex items-center gap-2 text-[13px]">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Aggregate over a fixed reporting period (otherwise each render is a live snapshot)
      </label>

      {enabled && (
        <>
          <div className="mt-3 grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Field label="Period type">
              <Select value={periodType} options={PERIOD_TYPES} labels={PERIOD_TYPE_LABELS}
                onChange={setPeriodType} />
            </Field>

            {!isCustom && (
              <Field label="Period rule">
                <Select value={periodRule} options={PERIOD_RULES} labels={PERIOD_RULE_LABELS}
                  onChange={setPeriodRule} />
              </Field>
            )}

            {showBoundary && (
              <Field label="Boundary offset (min after midnight)">
                <Input value={String(boundaryMin)}
                  onChange={(e) => setBoundaryMin(numOr(e.target.value, 0))}
                  placeholder="0  (e.g. 360 = 06:00)" />
              </Field>
            )}

            {isCustom && (
              <>
                <Field label="Start offset (min from current hour)">
                  <Input value={String(customStart)}
                    onChange={(e) => setCustomStart(numOr(e.target.value, 0))}
                    placeholder="-60" />
                </Field>
                <Field label="End offset (min from current hour)">
                  <Input value={String(customEnd)}
                    onChange={(e) => setCustomEnd(numOr(e.target.value, 0))}
                    placeholder="0" />
                </Field>
              </>
            )}

            <Field label="Missing period handling">
              <Select value={missing} options={MISSING} labels={MISSING_LABELS}
                onChange={setMissing} />
            </Field>
            <Field label="Period label format">
              <Input value={labelFormat} onChange={(e) => setLabelFormat(e.target.value)} />
            </Field>
            <Field label="Filename period format">
              <Input value={filenameFormat} onChange={(e) => setFilenameFormat(e.target.value)} />
            </Field>
          </div>

          {customInvalid && (
            <div className="mt-2 text-[12px]" style={{ color: "var(--ios-red, #c0392b)" }}>
              End offset must be greater than start offset.
            </div>
          )}
          <div className="mt-3 text-[12px]" style={{ color: "var(--ios-gray-1)" }}>
            {summary} Timestamps are stored in UTC and shown in plant time.
          </div>
        </>
      )}

      {!enabled && (
        <div className="mt-3 text-[12px]" style={{ color: "var(--ios-gray-1)" }}>
          This report uses live values at render time. Enable a period to aggregate
          historian data over a window (averages, totals, min/max — configured per tag
          in the Data tab).
        </div>
      )}
    </SectionCard>
  );
}
