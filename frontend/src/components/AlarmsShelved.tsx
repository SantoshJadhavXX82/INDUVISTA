/**
 * Phase 14.4 — Shelved alarms list.
 *
 * Lists every rule currently in state='shelved' with the time remaining
 * until the mute auto-expires. One-click Unshelve sends it back to
 * normal; the evaluator then re-evaluates the current value on its
 * next tick and will re-alarm if appropriate.
 *
 * Polls /api/alarms/shelved every 10s — slower than /active since
 * shelved rules are quieter by nature.
 */
import { useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Pause, Play, RefreshCw, AlertTriangle, Clock,
} from "lucide-react";

import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { SeverityBadge } from "@/components/SeverityBadge";
import { useTimeFormat } from "@/lib/timeFormat";

import type { AlarmActive, AlarmEvent } from "@/types/alarms";
import { RULE_TYPE_LABELS } from "@/types/alarms";

const REFRESH_MS = 10_000;

export default function AlarmsShelved() {
  const { formatDateTime } = useTimeFormat();
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ["alarms-shelved"],
    queryFn: () => api.get<AlarmActive[]>("/alarms/shelved"),
    refetchInterval: REFRESH_MS,
    staleTime: 0,
  });

  const unshelveMutation = useMutation({
    mutationFn: ({ ruleId }: { ruleId: number }) =>
      api.post<AlarmEvent>(`/alarms/rules/${ruleId}/unshelve`, {
        user_id: null,
        comment: null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alarms-shelved"] });
      qc.invalidateQueries({ queryKey: ["alarms-active"] });
      qc.invalidateQueries({ queryKey: ["alarms-history"] });
    },
  });

  const rows = useMemo(() => query.data ?? [], [query.data]);

  if (query.isLoading) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          Loading shelved alarms…
        </CardContent>
      </Card>
    );
  }

  if (query.isError) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-destructive">
          Failed to load shelved alarms: {(query.error as Error)?.message ?? "unknown"}
        </CardContent>
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 flex flex-col items-center gap-3 text-muted-foreground">
          <Pause className="h-10 w-10 opacity-30" />
          <p className="text-sm">No alarms are currently shelved.</p>
          <p className="text-[11px]">
            Click <strong>Shelve</strong> on an active alarm to mute it
            for a fixed duration.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-secondary/40 text-[10px] uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="text-left px-3 py-2 font-medium">Tag</th>
                <th className="text-left px-3 py-2 font-medium">Rule</th>
                <th className="text-left px-3 py-2 font-medium">Severity</th>
                <th className="text-right px-3 py-2 font-medium">Threshold</th>
                <th className="text-left px-3 py-2 font-medium whitespace-nowrap">
                  Expires
                </th>
                <th className="text-left px-3 py-2 font-medium whitespace-nowrap">
                  Time left
                </th>
                <th className="text-right px-3 py-2 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.rule_id}
                    className="border-t border-border hover:bg-secondary/30">
                  <td className="px-3 py-2 font-medium">{r.tag_name}</td>
                  <td className="px-3 py-2">
                    <Badge variant="outline" className="text-[10px] font-mono">
                      {RULE_TYPE_LABELS[r.rule_type]}
                    </Badge>
                  </td>
                  <td className="px-3 py-2">
                    <SeverityBadge code={r.severity} />
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatNumber(r.threshold)}
                    {r.engineering_unit && (
                      <span className="text-[10px] text-muted-foreground ml-1">
                        {r.engineering_unit}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs whitespace-nowrap text-muted-foreground">
                    {r.shelved_until
                      ? formatDateTime(r.shelved_until)
                      : "—"}
                  </td>
                  <td className="px-3 py-2 text-xs whitespace-nowrap">
                    <span className="inline-flex items-center gap-1">
                      <Clock className="h-3 w-3 text-indigo-600" />
                      {r.shelved_until
                        ? timeRemaining(r.shelved_until)
                        : "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-xs gap-1"
                      onClick={() =>
                        unshelveMutation.mutate({ ruleId: r.rule_id })}
                      disabled={
                        unshelveMutation.isPending &&
                        unshelveMutation.variables?.ruleId === r.rule_id
                      }
                    >
                      {unshelveMutation.isPending &&
                       unshelveMutation.variables?.ruleId === r.rule_id ? (
                        <RefreshCw className="h-3 w-3 animate-spin" />
                      ) : (
                        <Play className="h-3 w-3" />
                      )}
                      Unshelve
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {query.isFetching && (
          <div className="text-[10px] text-muted-foreground px-3 py-2 border-t border-border">
            <RefreshCw className="h-3 w-3 animate-spin inline mr-1" />
            Refreshing…
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatNumber(v: number): string {
  const abs = Math.abs(v);
  let s: string;
  if (abs >= 1000)    s = v.toFixed(1);
  else if (abs >= 1)  s = v.toFixed(3);
  else                s = v.toFixed(4);
  return s.replace(/\.?0+$/, "");
}

function timeRemaining(iso: string): string {
  const target = new Date(iso).getTime();
  const now = Date.now();
  const sec = Math.max(0, Math.floor((target - now) / 1000));
  if (sec === 0) return "expiring…";
  if (sec < 60)   return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return m === 0 ? `${h}h` : `${h}h ${m}m`;
  }
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  return h === 0 ? `${d}d` : `${d}d ${h}h`;
}
