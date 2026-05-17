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
import { useSeverityColors, hexWithAlpha } from "@/components/SeverityBadge";
import { useSeverities } from "@/lib/useSeverities";

import type { AlarmActive, Severity } from "@/types/alarms";
import { SEVERITY_RANK } from "@/types/alarms";

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

  // Phase 14.8 — counter chips are now driven by the live
  // alarm_severities list (sorted by rank) instead of a hardcoded
  // critical/high/medium/low/info array. Custom severities defined
  // under Setup show up here automatically.
  const { data: severitiesData } = useSeverities();
  const severitiesList = useMemo(
    () => [...(severitiesData ?? [])].sort((a, b) => a.rank - b.rank),
    [severitiesData],
  );

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
            {severitiesList.map((s) => (
              <SeverityCounter
                key={s.code}
                severity={s.code}
                count={counts[s.code] ?? 0}
                muted={(counts[s.code] ?? 0) === 0}
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

function severityRollup(rows: AlarmActive[]): Record<string, number> {
  // Phase 14.8 — open map rather than fixed-key Record<Severity>. Any
  // code that appears in the active list gets a counter; custom
  // severities are first-class.
  const out: Record<string, number> = {};
  for (const r of rows) out[r.severity] = (out[r.severity] ?? 0) + 1;
  return out;
}

function SeverityCounter({
  severity, count, muted,
}: { severity: Severity; count: number; muted: boolean }) {
  // Phase 14.8 — colors pulled from alarm_severities.color_hex via
  // useSeverityColors. Custom severities defined under Setup now show
  // up with their configured colour in the header chip row too.
  const { color, label } = useSeverityColors(severity);
  return (
    <div
      className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border ${
        muted ? "opacity-50" : ""
      }`}
      style={{
        borderColor: hexWithAlpha(color, 0.4),
        backgroundColor: hexWithAlpha(color, 0.06),
      }}
    >
      <span className="w-2 h-2 rounded-full"
            style={{ backgroundColor: color }} />
      <span className="text-xs font-medium">{label}</span>
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
