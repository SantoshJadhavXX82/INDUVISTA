/**
 * Phase 13.2 / 13.3 — Trend page.
 *
 * Two modes:
 *   - Historical: pick a fixed start/end (presets or custom). No polling.
 *   - Real-Time:  rolling window ending at "now", refetch every 5s.
 *
 * Real-Time implementation notes:
 *   - The query key uses `liveWindowMinutes` (an integer) instead of the
 *     computed start/end timestamps. Timestamps would change every render
 *     and invalidate the query continuously. The actual start/end are
 *     computed inside queryFn at fetch time.
 *   - Pause stops the refetchInterval but keeps the current view. Tag-
 *     selection changes still trigger a refetch via key change — operators
 *     can still build/edit a tag set while paused.
 *   - Mode switches preserve the pause state. If you paused live, switched
 *     to historical, then switched back to live, you're still paused.
 *     Predictable.
 *
 * Spec mapping: §6.1 / §6.2 / §6.3 / §6.4 (Pause/Resume).
 * Snapshot (§6.4) and Clear (§6.4) are deferred to 13.3d.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  TrendingUp, RefreshCw, AlertTriangle, Pause, Play, Camera,
} from "lucide-react";
import { api } from "@/lib/api";
import type { TrendHistoryResponse } from "@/types/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import TrendChart, { type TrendChartHandle } from "@/components/TrendChart";
import TagPicker from "@/components/TagPicker";
import TimeRangePicker, {
  type TimeRange,
  makePresetRange,
} from "@/components/TimeRangePicker";
import TrendSummaryPanel from "@/components/TrendSummaryPanel";

type Mode = "historical" | "live";

const LIVE_WINDOW_OPTIONS: { minutes: number; label: string }[] = [
  { minutes: 15,   label: "Last 15m" },
  { minutes: 60,   label: "Last 1h" },
  { minutes: 360,  label: "Last 6h" },
  { minutes: 1440, label: "Last 24h" },
];

const LIVE_POLL_INTERVAL_MS = 5_000;

export default function Trend() {
  const [mode, setMode] = useState<Mode>("historical");
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [historicalRange, setHistoricalRange] = useState<TimeRange>(() =>
    makePresetRange(60, "Last 1 h"),
  );
  const [liveWindowMinutes, setLiveWindowMinutes] = useState(15);
  const [paused, setPaused] = useState(false);

  // Imperative handle on the chart — used by the PNG snapshot button to
  // trigger a canvas download without exposing uPlot internals here.
  const chartRef = useRef<TrendChartHandle>(null);

  const historyQuery = useQuery({
    // KEY stability: in live mode, we use liveWindowMinutes (an int) as
    // the dynamic part — NOT the moving start/end timestamps. The actual
    // now() is computed inside queryFn at fetch time. This keeps the
    // cache key stable across renders and lets refetchInterval drive
    // periodic refreshes correctly.
    queryKey: [
      "trend-history",
      mode,
      selectedIds,
      mode === "historical"
        ? `${historicalRange.start}|${historicalRange.end}`
        : liveWindowMinutes,
    ],
    queryFn: () => {
      let start: string;
      let end: string;
      if (mode === "historical") {
        start = historicalRange.start;
        end = historicalRange.end;
      } else {
        // Rolling window ending at now.
        end = new Date().toISOString();
        start = new Date(Date.now() - liveWindowMinutes * 60_000).toISOString();
      }
      const params = new URLSearchParams({
        tag_ids: selectedIds.join(","),
        start, end,
        aggregation: "auto",
        max_points: "2000",
      });
      return api.get<TrendHistoryResponse>(`/trends/history?${params}`);
    },
    enabled: selectedIds.length > 0,
    refetchInterval: mode === "live" && !paused ? LIVE_POLL_INTERVAL_MS : false,
    refetchOnWindowFocus: false,
    staleTime: 0,
  });

  // Tick every 5s so "Updated Xs ago" stays accurate even when nothing
  // else is re-rendering the page.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 5_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="space-y-3 p-4">
      {/* ============ Header / Toolbar ============================== */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <CardTitle className="flex items-center gap-3 text-base">
              <span className="flex items-center gap-2">
                <TrendingUp className="h-4 w-4" />
                Trend
              </span>
              <ModeToggle value={mode} onChange={setMode} />
              <ModeBadge mode={mode} paused={paused} />
            </CardTitle>
            <div className="flex items-center gap-2 flex-wrap">
              {historyQuery.dataUpdatedAt > 0 && (
                <span className="text-[11px] text-muted-foreground tabular-nums">
                  Updated {formatRelative(historyQuery.dataUpdatedAt)}
                </span>
              )}
              {mode === "historical" ? (
                <TimeRangePicker
                  value={historicalRange}
                  onChange={setHistoricalRange}
                />
              ) : (
                <LiveWindowSelector
                  value={liveWindowMinutes}
                  onChange={setLiveWindowMinutes}
                />
              )}
              {mode === "live" && (
                <Button
                  variant={paused ? "default" : "outline"}
                  size="sm"
                  className="h-8 text-xs gap-1.5"
                  onClick={() => setPaused((v) => !v)}
                  title={paused
                    ? "Resume 5-second polling"
                    : "Pause polling — chart freezes on the current window"}
                >
                  {paused
                    ? <><Play className="h-3 w-3" /> Resume</>
                    : <><Pause className="h-3 w-3" /> Pause</>
                  }
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                className="h-8 text-xs gap-1.5"
                onClick={() => historyQuery.refetch()}
                disabled={selectedIds.length === 0 || historyQuery.isFetching}
              >
                <RefreshCw
                  className={`h-3 w-3 ${historyQuery.isFetching ? "animate-spin" : ""}`}
                />
                Refresh
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <TagPicker
            selectedIds={selectedIds}
            onChange={setSelectedIds}
            maxTags={6}
          />
        </CardContent>
      </Card>

      {/* ============ Chart card ==================================== */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {selectedIds.length > 0 && historyQuery.data
                ? `${historyQuery.data.series.length} tag${historyQuery.data.series.length === 1 ? "" : "s"} · ${currentWindowLabel(mode, historicalRange, liveWindowMinutes)} · ${describeAggregation(historyQuery.data)}`
                : "Chart"}
            </CardTitle>
            {historyQuery.data && (
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span>
                  <b className="text-foreground tabular-nums">
                    {historyQuery.data.series.reduce((s, x) => s + x.raw_count, 0).toLocaleString()}
                  </b>
                  {" "}raw samples
                </span>
                <span>
                  rendered{" "}
                  <b className="text-foreground tabular-nums">
                    {historyQuery.data.series.reduce((s, x) => s + x.returned_count, 0).toLocaleString()}
                  </b>
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-[11px] gap-1.5 px-2"
                  onClick={() => chartRef.current?.exportPNG()}
                  title="Download the current chart view as a PNG image"
                >
                  <Camera className="h-3 w-3" />
                  PNG
                </Button>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {selectedIds.length === 0 && (
            <div className="h-[420px] flex flex-col items-center justify-center text-center text-muted-foreground gap-2 border border-dashed border-border rounded-md">
              <TrendingUp className="h-8 w-8 opacity-40" />
              <p className="text-sm">No tags selected.</p>
              <p className="text-xs">Pick one or more tags above to see their history.</p>
            </div>
          )}

          {selectedIds.length > 0 && historyQuery.isLoading && (
            <div className="h-[420px] flex items-center justify-center text-muted-foreground">
              <RefreshCw className="h-4 w-4 animate-spin mr-2" />
              <span className="text-sm">Loading {selectedIds.length} tag(s)…</span>
            </div>
          )}

          {historyQuery.isError && (
            <div className="h-[420px] flex flex-col items-center justify-center text-center text-destructive gap-2">
              <AlertTriangle className="h-6 w-6" />
              <p className="text-sm font-medium">Failed to load trend data</p>
              <p className="text-xs">{(historyQuery.error as Error)?.message}</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2 text-xs"
                onClick={() => historyQuery.refetch()}
              >
                Retry
              </Button>
            </div>
          )}

          {historyQuery.data &&
            historyQuery.data.series.every((s) => s.returned_count === 0) && (
            <div className="h-[420px] flex flex-col items-center justify-center text-center text-muted-foreground gap-2 border border-dashed border-border rounded-md">
              <p className="text-sm">No data in the selected time range.</p>
              <p className="text-xs">
                Try widening the range or picking a tag that's actively polling.
              </p>
            </div>
          )}

          {historyQuery.data &&
            historyQuery.data.series.some((s) => s.returned_count > 0) && (
            <TrendChart ref={chartRef} history={historyQuery.data} height={440} />
          )}
        </CardContent>
      </Card>

      {/* ============ Summary panel ================================
          Uses the actual start/end from the last successful fetch so it
          stays aligned with whatever the chart is currently showing — even
          across pause/resume and mode switches. */}
      {selectedIds.length > 0 && historyQuery.data && (
        <TrendSummaryPanel
          tagIds={selectedIds}
          start={historyQuery.data.start}
          end={historyQuery.data.end}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ModeToggle({ value, onChange }: { value: Mode; onChange: (v: Mode) => void }) {
  return (
    <div className="flex bg-secondary rounded-md p-0.5 text-xs">
      <button
        type="button"
        onClick={() => onChange("historical")}
        className={`px-2.5 py-1 rounded transition-colors ${value === "historical" ? "bg-card font-medium shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
      >
        Historical
      </button>
      <button
        type="button"
        onClick={() => onChange("live")}
        className={`px-2.5 py-1 rounded transition-colors ${value === "live" ? "bg-card font-medium shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
      >
        Real-Time
      </button>
    </div>
  );
}

function ModeBadge({ mode, paused }: { mode: Mode; paused: boolean }) {
  if (mode === "historical") {
    return (
      <Badge variant="outline" className="text-[10px] font-normal">
        Historical
      </Badge>
    );
  }
  if (paused) {
    return (
      <Badge
        variant="outline"
        className="text-[10px] font-normal border-amber-300 text-amber-800 bg-amber-50 gap-1"
      >
        <Pause className="h-2.5 w-2.5" />
        Paused
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="text-[10px] font-normal border-red-300 text-red-800 bg-red-50 gap-1.5"
    >
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
      Live
    </Badge>
  );
}

function LiveWindowSelector({
  value, onChange,
}: {
  value: number;
  onChange: (m: number) => void;
}) {
  return (
    <div className="flex bg-secondary rounded-md p-0.5 text-xs">
      {LIVE_WINDOW_OPTIONS.map((opt) => (
        <button
          key={opt.minutes}
          type="button"
          onClick={() => onChange(opt.minutes)}
          className={`px-2.5 py-1 rounded transition-colors ${value === opt.minutes ? "bg-card font-medium shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function describeAggregation(history: TrendHistoryResponse): string {
  switch (history.aggregation) {
    case "raw": return "raw samples";
    case "1m":  return "1-minute buckets";
    case "1h":  return "1-hour buckets";
    case "1d":  return "1-day buckets";
  }
}

function currentWindowLabel(
  mode: Mode,
  historicalRange: TimeRange,
  liveWindowMinutes: number,
): string {
  if (mode === "historical") return historicalRange.label;
  const opt = LIVE_WINDOW_OPTIONS.find((o) => o.minutes === liveWindowMinutes);
  return opt?.label ?? `Last ${liveWindowMinutes}m`;
}

function formatRelative(ts: number): string {
  const ageSec = Math.floor((Date.now() - ts) / 1000);
  if (ageSec < 5) return "just now";
  if (ageSec < 60) return `${ageSec}s ago`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ago`;
  return `${Math.floor(ageSec / 3600)}h ago`;
}
