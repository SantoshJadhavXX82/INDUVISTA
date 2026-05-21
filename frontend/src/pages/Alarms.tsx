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
 *
 * Phase 18 visual refactor — page now uses the iOS-style primitives:
 *   - PageHeader for title/subtitle/actions
 *   - SeverityCardStrip in place of the old bordered counter chips
 *     (bigger numbers, softer iOS cards, but still driven by the live
 *      alarm_severities DB rows so custom colors propagate)
 *   - iOS pill-style tab strip in place of the underlined tabs
 *   - SectionCard wraps each tab body
 *
 * No data flow changes — the same queries, mutations, and child
 * components are used. Visual only.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bell, BellRing, History, Settings, RefreshCw, Pause, Flame } from "lucide-react";

import { api } from "@/lib/api";
import { useSeverityColors, hexWithAlpha } from "@/components/SeverityBadge";
import { useSeverities } from "@/lib/useSeverities";

import type { AlarmActive, Severity } from "@/types/alarms";

import AlarmsActive from "@/components/AlarmsActive";
import AlarmsHistory from "@/components/AlarmsHistory";
import AlarmsRules from "@/components/AlarmsRules";
import AlarmsShelved from "@/components/AlarmsShelved";

import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { AlarmDensityHeatmapCard } from "@/components/alarms/alarm-density-heatmap";
import { cn } from "@/lib/utils";

type Tab = "active" | "shelved" | "history" | "rules" | "patterns";

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

  const { data: severitiesData } = useSeverities();
  const severitiesList = useMemo(
    () => [...(severitiesData ?? [])].sort((a, b) => a.rank - b.rank),
    [severitiesData],
  );

  return (
    <div className="flex flex-col gap-4 max-w-7xl mx-auto">
      <PageHeader
        title="Alarms"
        subtitle={
          totalActive === 0
            ? "No active alarms · system clean"
            : `${totalActive} active · refreshing every ${REFRESH_MS / 1000}s`
        }
        actions={
          <div className="flex items-center gap-2">
            {totalActive > 0 ? (
              <BellRing
                className="h-4 w-4 animate-pulse"
                style={{ color: "var(--ios-red)" }}
              />
            ) : (
              <Bell className="h-4 w-4" style={{ color: "var(--ios-gray-1)" }} />
            )}
            {activeQuery.isFetching && (
              <RefreshCw
                className="h-3.5 w-3.5 animate-spin"
                style={{ color: "var(--ios-gray-1)" }}
              />
            )}
          </div>
        }
      />

      {/* Severity strip — iOS metric cards driven by the live
          alarm_severities table. Custom severities defined under Setup
          show with their configured colors automatically. */}
      <SeverityCardStrip
        severities={severitiesList}
        counts={counts}
      />

      {/* iOS pill-style tab strip */}
      <div
        className="flex items-center gap-1 p-1 rounded-lg"
        style={{
          backgroundColor: "var(--bg-elevated-soft)",
          alignSelf: "flex-start",
        }}
      >
        <PillTab active={tab === "active"} onClick={() => setTab("active")}>
          <BellRing className="h-3.5 w-3.5" />
          Active
          {totalActive > 0 && <TabBadge>{totalActive}</TabBadge>}
        </PillTab>
        <PillTab active={tab === "shelved"} onClick={() => setTab("shelved")}>
          <Pause className="h-3.5 w-3.5" />
          Shelved
          {totalShelved > 0 && <TabBadge tone="indigo">{totalShelved}</TabBadge>}
        </PillTab>
        <PillTab active={tab === "history"} onClick={() => setTab("history")}>
          <History className="h-3.5 w-3.5" />
          History
        </PillTab>
        <PillTab active={tab === "rules"} onClick={() => setTab("rules")}>
          <Settings className="h-3.5 w-3.5" />
          Rules
        </PillTab>
        <PillTab active={tab === "patterns"} onClick={() => setTab("patterns")}>
          <Flame className="h-3.5 w-3.5" />
          Patterns
        </PillTab>
      </div>

      {/* Tab body wrapped in an iOS-style SectionCard. The card has flush
          padding so the existing child components can manage their own
          internal spacing.

          Note: Patterns gets the Card wrapper that hosts its own window
          picker, so it sits OUTSIDE the flush wrapper — otherwise nested
          SectionCards would look weird. */}
      {tab === "patterns" ? (
        <AlarmDensityHeatmapCard />
      ) : (
        <SectionCard flush>
          <div className="p-3">
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
        </SectionCard>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// SeverityCardStrip — iOS metric strip with custom DB-driven colors
//
// Each card shows: colored dot, severity label, big tabular count. Uses
// the live alarm_severities color_hex via useSeverityColors so admin-
// configured custom colors propagate here without code changes. Cards
// for zero-count severities are dimmed but stay in the row so the layout
// doesn't reflow when a severity goes from 0 → N.
// ---------------------------------------------------------------------------

function SeverityCardStrip({
  severities, counts,
}: {
  severities: Array<{ code: string; rank: number }>;
  counts: Record<string, number>;
}) {
  if (severities.length === 0) {
    return null;
  }
  return (
    <div
      className="grid gap-2"
      style={{
        gridTemplateColumns: `repeat(${severities.length}, minmax(0, 1fr))`,
      }}
    >
      {severities.map((s) => (
        <SeverityCard
          key={s.code}
          severity={s.code as Severity}
          count={counts[s.code] ?? 0}
        />
      ))}
    </div>
  );
}


function SeverityCard({
  severity, count,
}: { severity: Severity; count: number }) {
  const { color, label } = useSeverityColors(severity);
  const muted = count === 0;
  return (
    <div
      className="min-w-0 transition-opacity"
      style={{
        backgroundColor: "var(--bg-elevated)",
        borderRadius: "var(--radius-lg-2)",
        padding: "10px 14px",
        opacity: muted ? 0.55 : 1,
      }}
    >
      <div
        className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider truncate"
        style={{ color: "var(--ios-gray-1)" }}
      >
        <span
          aria-hidden="true"
          className="inline-block rounded-full shrink-0"
          style={{ width: 7, height: 7, backgroundColor: color }}
        />
        {label}
      </div>
      <div
        className="font-semibold tabular-nums leading-tight mt-1"
        style={{
          fontSize: 22,
          letterSpacing: "-0.02em",
          color: muted ? "var(--ios-gray-1)" : "#000",
        }}
      >
        {count}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// PillTab — iOS-style segmented control tab
//
// Sits inside a soft-gray pill container. Active tab gets a white "raised"
// pill with a tiny shadow, inactive tabs are transparent text. This is
// the iOS segmented-control pattern, more compact and modern than the
// underlined tabs the page used previously.
// ---------------------------------------------------------------------------

function PillTab({
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
      className={cn(
        "flex items-center gap-1.5 px-3 py-1.5 text-[12px] font-medium",
        "rounded-md transition-all",
      )}
      style={
        active
          ? {
              backgroundColor: "var(--bg-elevated)",
              color: "var(--text-primary)",
              boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
            }
          : {
              color: "var(--text-secondary)",
              backgroundColor: "transparent",
            }
      }
    >
      {children}
    </button>
  );
}


function TabBadge({
  children, tone = "neutral",
}: { children: React.ReactNode; tone?: "neutral" | "indigo" }) {
  return (
    <span
      className="text-[10px] font-semibold tabular-nums px-1.5 py-px rounded-full"
      style={{
        backgroundColor: tone === "indigo" ? "var(--ios-indigo-soft)" : "var(--ios-gray-6)",
        color: tone === "indigo" ? "var(--ios-indigo-on-soft)" : "var(--ios-gray-1)",
        minWidth: 18,
        textAlign: "center",
      }}
    >
      {children}
    </span>
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function severityRollup(rows: AlarmActive[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) out[r.severity] = (out[r.severity] ?? 0) + 1;
  return out;
}
