/**
 * Phase 14.5 — Active alarms list.
 * Phase 14.4 — adds Shelve action alongside Ack.
 *
 * Renders the operator's primary view: every alarm currently in one of
 * active_unack / active_ack / inactive_unack. Sorted by severity
 * (critical first) then most-recent change. One-click acknowledge
 * mutates state via /api/alarms/rules/{id}/ack. Shelve opens a dialog
 * for duration selection, then POSTs to /api/alarms/rules/{id}/shelve.
 *
 * Polling is owned by the parent Alarms page so header counts stay in
 * sync — this component receives `data` as a prop rather than running
 * its own useQuery.
 */
import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, AlertOctagon, AlertCircle,
  Check, RefreshCw, Bell, Pause,
} from "lucide-react";

import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useTimeFormat } from "@/lib/timeFormat";
import ShelveDialog from "@/components/ShelveDialog";
import { useSeverityColors, hexWithAlpha } from "@/components/SeverityBadge";
import { Gate } from "@/lib/rbac";
import { useSeverities } from "@/lib/useSeverities";

import type { AlarmActive, AlarmEvent, Severity, StateValue } from "@/types/alarms";
import {
  RULE_TYPE_LABELS, SEVERITY_RANK, STATE_LABELS,
} from "@/types/alarms";

interface Props {
  data: AlarmActive[] | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  refetch: () => void;
}

export default function AlarmsActive({
  data, isLoading, isError, error, refetch,
}: Props) {
  const { formatDateTime } = useTimeFormat();
  const qc = useQueryClient();

  // Shelve dialog state. `target` carries enough info to render the
  // dialog header and fire the mutation when the operator confirms.
  const [shelveTarget, setShelveTarget] = useState<AlarmActive | null>(null);
  const [shelveError, setShelveError] = useState<string | null>(null);

  // Defensive client-side sort: severity desc (rank asc = more urgent),
  // then last_change desc. Phase 14.8 — uses the live alarm_severities
  // rank, so custom severities slot in at their configured position.
  // Falls back to the hardcoded SEVERITY_RANK for system codes during
  // the brief window before useSeverities loads.
  const { data: severities } = useSeverities();
  const sorted = useMemo(() => {
    if (!data) return [];
    const ranks = new Map<string, number>(
      (severities ?? []).map((s) => [s.code, s.rank])
    );
    const rankOf = (code: string): number =>
      ranks.get(code) ?? SEVERITY_RANK[code as Severity] ?? 999;
    return [...data].sort((a, b) => {
      const s = rankOf(a.severity) - rankOf(b.severity);
      if (s !== 0) return s;
      return b.last_change_time.localeCompare(a.last_change_time);
    });
  }, [data, severities]);

  const ackMutation = useMutation({
    mutationFn: ({ ruleId }: { ruleId: number }) =>
      api.post<AlarmEvent>(`/alarms/rules/${ruleId}/ack`, {
        user_id: null,
        comment: null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alarms-active"] });
      qc.invalidateQueries({ queryKey: ["alarms-history"] });
    },
  });

  const shelveMutation = useMutation({
    mutationFn: ({ ruleId, duration_minutes, comment }: {
      ruleId: number; duration_minutes: number; comment: string | null;
    }) =>
      api.post<AlarmEvent>(`/alarms/rules/${ruleId}/shelve`, {
        duration_minutes,
        user_id: null,
        comment,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alarms-active"] });
      qc.invalidateQueries({ queryKey: ["alarms-shelved"] });
      qc.invalidateQueries({ queryKey: ["alarms-history"] });
      setShelveTarget(null);
      setShelveError(null);
    },
    onError: (e: unknown) => {
      setShelveError(e instanceof Error ? e.message : String(e));
    },
  });

  // ---- empty / loading / error states ----

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          Loading active alarms…
        </CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-destructive">
          Failed to load active alarms: {error?.message ?? "unknown error"}
          <Button variant="outline" size="sm" className="ml-3 h-7 text-xs"
                  onClick={() => refetch()}>
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (sorted.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 flex flex-col items-center gap-3 text-muted-foreground">
          <Bell className="h-10 w-10 opacity-30" />
          <p className="text-sm">No active alarms. All good.</p>
          <p className="text-[11px]">
            New alarms appear here within ~5 seconds of being triggered
            by the evaluator.
          </p>
        </CardContent>
      </Card>
    );
  }

  // ---- the list ----

  return (
    <>
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-secondary/40 text-[10px] uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="w-1 p-0" /> {/* severity colour bar */}
                  <th className="text-left px-3 py-2 font-medium">Tag</th>
                  <th className="text-left px-3 py-2 font-medium">Rule</th>
                  <th className="text-right px-3 py-2 font-medium">Threshold</th>
                  <th className="text-right px-3 py-2 font-medium">Current</th>
                  <th className="text-left px-3 py-2 font-medium">State</th>
                  <th className="text-left px-3 py-2 font-medium whitespace-nowrap">Since</th>
                  <th className="text-right px-3 py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((a) => (
                  <ActiveRow
                    key={a.rule_id}
                    alarm={a}
                    acking={
                      ackMutation.isPending &&
                      ackMutation.variables?.ruleId === a.rule_id
                    }
                    shelving={
                      shelveMutation.isPending &&
                      shelveMutation.variables?.ruleId === a.rule_id
                    }
                    onAck={() => ackMutation.mutate({ ruleId: a.rule_id })}
                    onShelve={() => {
                      setShelveError(null);
                      setShelveTarget(a);
                    }}
                    formatDateTime={formatDateTime}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <ShelveDialog
        open={shelveTarget != null}
        ruleLabel={
          shelveTarget
            ? `${shelveTarget.tag_name} (${RULE_TYPE_LABELS[shelveTarget.rule_type]})`
            : ""
        }
        onConfirm={(minutes, comment) => {
          if (!shelveTarget) return;
          shelveMutation.mutate({
            ruleId: shelveTarget.rule_id,
            duration_minutes: minutes,
            comment,
          });
        }}
        onCancel={() => {
          setShelveTarget(null);
          setShelveError(null);
        }}
        pending={shelveMutation.isPending}
        error={shelveError}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function ActiveRow({
  alarm, acking, shelving, onAck, onShelve, formatDateTime,
}: {
  alarm: AlarmActive;
  acking: boolean;
  shelving: boolean;
  onAck: () => void;
  onShelve: () => void;
  formatDateTime: (iso: string) => string;
}) {
  const canAck = alarm.state === "active_unack" || alarm.state === "inactive_unack";
  // Shelve from any active-ish state. Don't allow from inactive_unack
  // because the operator should ack-to-close the latched alarm, not
  // mute it (semantically distinct concerns).
  const canShelve = alarm.state === "active_unack" || alarm.state === "active_ack";
  // Phase 14.8 — colours come from alarm_severities.color_hex so
  // operator-defined custom severities use their configured colour.
  const { color: severityColor } = useSeverityColors(alarm.severity);
  const barStyle = { backgroundColor: severityColor };
  const rowStyle = alarm.state === "active_unack"
    ? { backgroundColor: hexWithAlpha(severityColor, 0.08) }
    : undefined;

  return (
    <tr className="border-t border-border hover:bg-secondary/30" style={rowStyle}>
      <td className="p-0 w-1" style={barStyle} />
      <td className="px-3 py-2">
        <div className="flex items-center gap-2">
          <SeverityIcon severity={alarm.severity} />
          <div>
            <div className="font-medium">{alarm.tag_name}</div>
            {alarm.message_template && (
              <div className="text-[11px] text-muted-foreground">
                {alarm.message_template}
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="px-3 py-2 text-xs">
        <Badge variant="outline" className="font-mono">
          {RULE_TYPE_LABELS[alarm.rule_type]}
        </Badge>
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {formatNumber(alarm.threshold)}
        {alarm.engineering_unit && (
          <span className="text-[10px] text-muted-foreground ml-1">
            {alarm.engineering_unit}
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        <span className={alarm.current_quality != null && alarm.current_quality < 128
          ? "text-amber-700"
          : ""}>
          {alarm.current_value != null ? formatNumber(alarm.current_value) : "—"}
        </span>
        {alarm.engineering_unit && alarm.current_value != null && (
          <span className="text-[10px] text-muted-foreground ml-1">
            {alarm.engineering_unit}
          </span>
        )}
      </td>
      <td className="px-3 py-2">
        <StateBadge state={alarm.state} />
      </td>
      <td className="px-3 py-2 text-xs whitespace-nowrap">
        <span title={`Since ${formatDateTime(alarm.last_change_time)}`}>
          {timeAgo(alarm.last_change_time)}
        </span>
      </td>
      <td className="px-3 py-2 text-right">
        <div className="inline-flex items-center gap-1">
          {canShelve && (
            <Gate cap="operate" mode="disable">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1"
              onClick={onShelve}
              disabled={shelving}
              title="Mute this alarm for a fixed duration"
            >
              {shelving ? (
                <RefreshCw className="h-3 w-3 animate-spin" />
              ) : (
                <Pause className="h-3 w-3" />
              )}
              Shelve
            </Button>
            </Gate>
          )}
          {canAck ? (
            <Gate cap="operate" mode="disable">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1"
              onClick={onAck}
              disabled={acking}
            >
              {acking ? (
                <RefreshCw className="h-3 w-3 animate-spin" />
              ) : (
                <Check className="h-3 w-3" />
              )}
              Ack
            </Button>
            </Gate>
          ) : (
            !canShelve && (
              <span className="text-[10px] text-muted-foreground">acked</span>
            )
          )}
        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Badges + helpers (unchanged from 14.5 baseline)
// ---------------------------------------------------------------------------

function SeverityIcon({ severity }: { severity: Severity }) {
  // Phase 14.8 — icon shape stays semantic (rank-tier picks the
  // glyph: octagon for top tier, triangle for mid, circle for low),
  // but the colour is pulled from alarm_severities.color_hex so
  // custom severities use their configured palette.
  const { color, rank } = useSeverityColors(severity);
  const cls = "h-4 w-4 flex-shrink-0";
  // Map rank -> icon shape. Ranks 1 (most urgent) ... 5+ (least).
  // Use ranges so custom severities with arbitrary ranks still render.
  let Icon = AlertCircle;
  if (rank <= 1)       Icon = AlertOctagon;
  else if (rank <= 3)  Icon = AlertTriangle;
  else                 Icon = AlertCircle;
  return <Icon className={cls} style={{ color }} />;
}

function StateBadge({ state }: { state: StateValue }) {
  const label = STATE_LABELS[state];
  const cls = stateBadgeClass(state);
  return (
    <Badge variant="outline" className={`text-[10px] ${cls}`}>{label}</Badge>
  );
}

function stateBadgeClass(state: StateValue): string {
  switch (state) {
    case "active_unack":
      return "bg-red-50 text-red-800 border-red-300 font-semibold";
    case "active_ack":
      return "bg-orange-50 text-orange-800 border-orange-300";
    case "inactive_unack":
      return "bg-amber-50 text-amber-800 border-amber-300";
    case "shelved":
      return "bg-indigo-50 text-indigo-800 border-indigo-300";
    case "disabled":
      return "bg-slate-50 text-slate-600 border-slate-300";
    case "normal":
      return "bg-emerald-50 text-emerald-800 border-emerald-300";
  }
}

function formatNumber(v: number): string {
  const abs = Math.abs(v);
  let s: string;
  if (abs >= 1000)    s = v.toFixed(1);
  else if (abs >= 1)  s = v.toFixed(3);
  else                s = v.toFixed(4);
  return s.replace(/\.?0+$/, "");
}

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const sec = Math.max(0, Math.floor((now - then) / 1000));
  if (sec < 60)   return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return m === 0 ? `${h}h ago` : `${h}h ${m}m ago`;
  }
  const d = Math.floor(sec / 86400);
  return `${d}d ago`;
}
