/**
 * Phase 14.5 — Alarms page.
 *
 * Three tabs:
 *   - Active  : currently-firing alarms; the operator's primary view.
 *               Auto-refreshes every 5 s.
 *   - History : full event log, paginated and filterable.
 *   - Rules   : CRUD on alarm rule configuration.
 *
 * Header shows severity counts (rolled up from the active list) so the
 * operator can see at a glance whether anything urgent is happening
 * without needing to be on the Active tab.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bell, BellRing, History, Settings, RefreshCw, Pause } from "lucide-react";

import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import type { AlarmActive, Severity } from "@/types/alarms";
import { SEVERITY_LABELS, SEVERITY_RANK } from "@/types/alarms";

import AlarmsActive from "@/components/AlarmsActive";
import AlarmsHistory from "@/components/AlarmsHistory";
import AlarmsRules from "@/components/AlarmsRules";
import AlarmsShelved from "@/components/AlarmsShelved";

type Tab = "active" | "shelved" | "history" | "rules";

const REFRESH_MS = 5_000;

export default function Alarms() {
  const [tab, setTab] = useState<Tab>("active");

  // Active alarms query lives here (not just inside AlarmsActive) so the
  // header counts can show even when the operator is on another tab.
  const activeQuery = useQuery({
    queryKey: ["alarms-active"],
    queryFn: () => api.get<AlarmActive[]>("/alarms/active"),
    refetchInterval: REFRESH_MS,
    refetchOnWindowFocus: true,
    staleTime: 0,
  });

  // Shelved count for the tab badge. Slower poll than active — shelved
  // rules don't churn.
  const shelvedQuery = useQuery({
    queryKey: ["alarms-shelved-count"],
    queryFn: () => api.get<AlarmActive[]>("/alarms/shelved"),
    refetchInterval: REFRESH_MS * 2,
    staleTime: 0,
  });

  const counts = useMemo(() => severityRollup(activeQuery.data ?? []), [activeQuery.data]);
  const totalActive = activeQuery.data?.length ?? 0;
  const totalShelved = shelvedQuery.data?.length ?? 0;

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Header card with severity counters */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base font-medium flex items-center justify-between flex-wrap gap-3">
            <span className="flex items-center gap-2">
              {totalActive > 0 ? (
                <BellRing className="h-4 w-4 text-red-600 animate-pulse" />
              ) : (
                <Bell className="h-4 w-4 text-muted-foreground" />
              )}
              Alarms
              <span className="text-xs text-muted-foreground font-normal">
                {totalActive === 0
                  ? "No active alarms"
                  : `${totalActive} active`}
              </span>
            </span>
            {activeQuery.isFetching && (
              <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="flex items-center gap-2 flex-wrap">
            {(["critical", "high", "medium", "low", "info"] as Severity[]).map((s) => (
              <SeverityCounter
                key={s}
                severity={s}
                count={counts[s]}
                muted={counts[s] === 0}
              />
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Tab strip */}
      <div className="flex items-center gap-1 border-b border-border">
        <TabButton active={tab === "active"} onClick={() => setTab("active")}>
          <BellRing className="h-3.5 w-3.5" />
          Active
          {totalActive > 0 && (
            <Badge variant="outline" className="ml-1 text-[10px] px-1.5 py-0">
              {totalActive}
            </Badge>
          )}
        </TabButton>
        <TabButton active={tab === "shelved"} onClick={() => setTab("shelved")}>
          <Pause className="h-3.5 w-3.5" />
          Shelved
          {totalShelved > 0 && (
            <Badge variant="outline"
                   className="ml-1 text-[10px] px-1.5 py-0 bg-indigo-50 text-indigo-700 border-indigo-300">
              {totalShelved}
            </Badge>
          )}
        </TabButton>
        <TabButton active={tab === "history"} onClick={() => setTab("history")}>
          <History className="h-3.5 w-3.5" />
          History
        </TabButton>
        <TabButton active={tab === "rules"} onClick={() => setTab("rules")}>
          <Settings className="h-3.5 w-3.5" />
          Rules
        </TabButton>
      </div>

      {/* Tab body */}
      {tab === "active" && (
        <AlarmsActive
          data={activeQuery.data}
          isLoading={activeQuery.isLoading}
          isError={activeQuery.isError}
          error={activeQuery.error as Error | null}
          refetch={activeQuery.refetch}
        />
      )}
      {tab === "shelved" && <AlarmsShelved />}
      {tab === "history" && <AlarmsHistory />}
      {tab === "rules" && <AlarmsRules />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function severityRollup(rows: AlarmActive[]): Record<Severity, number> {
  const out: Record<Severity, number> = {
    critical: 0, high: 0, medium: 0, low: 0, info: 0,
  };
  for (const r of rows) out[r.severity]++;
  return out;
}

function SeverityCounter({
  severity, count, muted,
}: { severity: Severity; count: number; muted: boolean }) {
  return (
    <div
      className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border ${
        muted ? "opacity-50" : ""
      } ${severityBgClass(severity)}`}
    >
      <span className={`w-2 h-2 rounded-full ${severityDotClass(severity)}`} />
      <span className="text-xs font-medium">{SEVERITY_LABELS[severity]}</span>
      <span className="text-xs tabular-nums font-semibold">{count}</span>
    </div>
  );
}

function TabButton({
  active, onClick, children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors ${
        active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

function severityBgClass(s: Severity): string {
  switch (s) {
    case "critical": return "border-red-200 bg-red-50/50";
    case "high":     return "border-orange-200 bg-orange-50/50";
    case "medium":   return "border-amber-200 bg-amber-50/50";
    case "low":      return "border-blue-200 bg-blue-50/50";
    case "info":     return "border-slate-200 bg-slate-50/50";
  }
}
function severityDotClass(s: Severity): string {
  switch (s) {
    case "critical": return "bg-red-500";
    case "high":     return "bg-orange-500";
    case "medium":   return "bg-amber-500";
    case "low":      return "bg-blue-500";
    case "info":     return "bg-slate-400";
  }
}

// Exported for use by tab components so the severity palette stays
// consistent across the page.
export { severityBgClass, severityDotClass };
