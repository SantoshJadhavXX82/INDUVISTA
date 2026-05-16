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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  TrendingUp, RefreshCw, AlertTriangle, Pause, Play, Camera, Eraser, Timer, Clock,
} from "lucide-react";
import { api } from "@/lib/api";
import type { TrendHistoryResponse } from "@/types/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import TrendChart, { type TrendChartHandle } from "@/components/TrendChart";
import TagPicker from "@/components/TagPicker";
import TimeRangePicker, {
  type TimeRange,
  makePresetRange,
} from "@/components/TimeRangePicker";
import TrendSummaryPanel from "@/components/TrendSummaryPanel";
import RawDataTable from "@/components/RawDataTable";
import LiveValuePanel from "@/components/LiveValuePanel";
import SavedViews from "@/components/SavedViews";
import AggregationSelector, {
  type AggregationOption,
} from "@/components/AggregationSelector";
import AggregationModeSelector, {
  type AggregationMode, loadAggregationMode,
} from "@/components/AggregationModeSelector";
import TooltipModeSelector, {
  type TooltipMode, loadTooltipMode,
} from "@/components/TooltipModeSelector";
import QualityFilterSelector, {
  type QualityFilter, loadQualityFilter,
} from "@/components/QualityFilterSelector";
import { useTimeFormat } from "@/lib/timeFormat";
import type { TrendViewConfig } from "@/types/api";

type Mode = "historical" | "live";

// Spec §6.1 — Real-Time rolling windows: 1m, 5m, 15m, 30m, 1h, 8h.
// "Custom" rolling window is reachable by typing a value in the selector.
const LIVE_WINDOW_OPTIONS: { minutes: number; label: string }[] = [
  { minutes: 1,   label: "Last 1m" },
  { minutes: 5,   label: "Last 5m" },
  { minutes: 15,  label: "Last 15m" },
  { minutes: 30,  label: "Last 30m" },
  { minutes: 60,  label: "Last 1h" },
  { minutes: 480, label: "Last 8h" },
];

// Mirror of TimeRangePicker's rolling presets — used to detect whether the
// current historical range matches a named rolling preset (saved as a
// relative window) versus a fully custom or date-anchored range (saved as
// absolute timestamps). Date-anchored presets (Today / Yesterday / Current
// Week / etc.) deliberately fall through to absolute storage — see
// SavedViews and the load handler for the rationale.
const LIVE_WINDOW_OPTIONS_HIST: { minutes: number; label: string }[] = [
  { minutes: 5,     label: "Last 5 min" },
  { minutes: 15,    label: "Last 15 min" },
  { minutes: 60,    label: "Last 1 h" },
  { minutes: 480,   label: "Last 8 h" },
  { minutes: 1440,  label: "Last 24 h" },
];

// Spec §6.2 — configurable refresh interval. Independent of the polling
// interval used by communication drivers (Modbus, etc.).
const REFRESH_INTERVAL_OPTIONS: { sec: number; label: string }[] = [
  { sec: 1,  label: "1s" },
  { sec: 2,  label: "2s" },
  { sec: 5,  label: "5s" },
  { sec: 10, label: "10s" },
  { sec: 30, label: "30s" },
  { sec: 60, label: "1m" },
];

// Spec §5.4 — per-mode tag limits.
const MAX_TAGS_LIVE = 10;
const MAX_TAGS_HISTORICAL = 20;

export default function Trend() {
  const [mode, setMode] = useState<Mode>("historical");
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [historicalRange, setHistoricalRange] = useState<TimeRange>(() =>
    makePresetRange(60, "Last 1 h"),
  );
  const [liveWindowMinutes, setLiveWindowMinutes] = useState(15);
  const [paused, setPaused] = useState(false);

  // Spec §6.2 — refresh interval (seconds) used for the live polling cycle.
  const [refreshIntervalSec, setRefreshIntervalSec] = useState(5);

  // Spec §6.4 — Clear Buffer. When the operator presses Clear Buffer, the
  // visible live trend is "reset": the query window's start is clamped to
  // this timestamp until the natural rolling window catches up. After that
  // the value becomes irrelevant and the chart resumes normal behavior.
  const [bufferClearAt, setBufferClearAt] = useState<number | null>(null);

  // Spec 16.2/16.3 — operator-controlled aggregation interval. "auto"
  // routes by window size (raw <30min, 1m <4h, 1h <7d, 1d wider).
  const [aggregation, setAggregation] = useState<AggregationOption>("auto");

  // Spec 16.1 — aggregation MODE (last/first/avg/min/max). Ignored when
  // the effective interval is raw. Persisted across sessions.
  const [aggregationMode, setAggregationMode] =
    useState<AggregationMode>(loadAggregationMode);

  // Tooltip display preference — persists across sessions.
  const [tooltipMode, setTooltipMode] = useState<TooltipMode>(loadTooltipMode);

  // Spec 9.4 — quality filter. Persists across sessions.
  const [qualityFilter, setQualityFilter] = useState<QualityFilter>(loadQualityFilter);

  // Per-tag show/hide on the chart, driven by clicks on Live Value Panel
  // tiles. Prunes itself when a tag is removed from selection.
  const [hiddenTagIds, setHiddenTagIds] = useState<Set<number>>(new Set());
  const toggleHidden = useCallback((id: number) => {
    setHiddenTagIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  useEffect(() => {
    setHiddenTagIds((prev) => {
      const filtered = new Set(
        [...prev].filter((id) => selectedIds.includes(id)),
      );
      // Only update if changed - avoids extra renders.
      return filtered.size === prev.size ? prev : filtered;
    });
  }, [selectedIds]);

  // Imperative handle on the chart — used by the PNG snapshot button to
  // trigger a canvas download without exposing uPlot internals here.
  const chartRef = useRef<TrendChartHandle>(null);

  // Build the current view config that SavedViews can snapshot if the
  // operator clicks "Save". We compose it on each render — cheap and
  // always in sync with the live UI state.
  const currentConfig: TrendViewConfig = (() => {
    if (mode === "live") {
      const opt = LIVE_WINDOW_OPTIONS.find((o) => o.minutes === liveWindowMinutes);
      return {
        tag_ids: selectedIds,
        mode: "live",
        preset_minutes: liveWindowMinutes,
        preset_label: opt?.label ?? `Last ${liveWindowMinutes}m`,
      };
    }
    // Historical: store either a relative preset (operator picks "Last 1 h"
    // — we want re-applying the view tomorrow to mean "Last 1 h" tomorrow,
    // not the exact range it was saved in) OR absolute start/end (when the
    // operator picked a custom range, we save the exact times).
    const presetMatch = LIVE_WINDOW_OPTIONS_HIST.find(
      (o) => o.label === historicalRange.label,
    );
    if (presetMatch) {
      return {
        tag_ids: selectedIds,
        mode: "historical",
        preset_minutes: presetMatch.minutes,
        preset_label: presetMatch.label,
      };
    }
    return {
      tag_ids: selectedIds,
      mode: "historical",
      start: historicalRange.start,
      end: historicalRange.end,
      preset_label: historicalRange.label,
    };
  })();

  const handleLoadView = (config: TrendViewConfig) => {
    setSelectedIds(config.tag_ids);
    if (config.mode === "live") {
      setMode("live");
      setLiveWindowMinutes(config.preset_minutes ?? 15);
      setPaused(false);
    } else {
      setMode("historical");
      if (config.preset_minutes && config.preset_label) {
        // Relative preset — recompute from now so "Last 1 h" means
        // "the hour ending right now", not the original snapshot moment.
        setHistoricalRange(makePresetRange(config.preset_minutes, config.preset_label));
      } else if (config.start && config.end) {
        setHistoricalRange({
          start: config.start,
          end: config.end,
          label: config.preset_label ?? "Custom range",
        });
      }
    }
  };

  const historyQuery = useQuery({
    // KEY stability: in live mode, we use liveWindowMinutes (an int) as
    // the dynamic part — NOT the moving start/end timestamps. The actual
    // now() is computed inside queryFn at fetch time. This keeps the
    // cache key stable across renders and lets refetchInterval drive
    // periodic refreshes correctly.
    //
    // bufferClearAt is included in the live key so a Clear Buffer click
    // forces an immediate refetch with the new clamped start.
    queryKey: [
      "trend-history",
      mode,
      selectedIds,
      aggregation,
      aggregationMode,
      mode === "historical"
        ? `${historicalRange.start}|${historicalRange.end}`
        : `${liveWindowMinutes}|${bufferClearAt ?? 0}`,
    ],
    queryFn: () => {
      let start: string;
      let end: string;
      if (mode === "historical") {
        start = historicalRange.start;
        end = historicalRange.end;
      } else {
        // Rolling window ending at now, but never starting before the most
        // recent Clear Buffer (if any). Once the natural rolling start
        // moves past bufferClearAt, the clamp becomes a no-op.
        end = new Date().toISOString();
        const naturalStartMs = Date.now() - liveWindowMinutes * 60_000;
        const effectiveStartMs = bufferClearAt != null
          ? Math.max(naturalStartMs, bufferClearAt)
          : naturalStartMs;
        start = new Date(effectiveStartMs).toISOString();
      }
      const params = new URLSearchParams({
        tag_ids: selectedIds.join(","),
        start, end,
        aggregation,
        agg_mode: aggregationMode,
        max_points: "2000",
      });
      return api.get<TrendHistoryResponse>(`/trends/history?${params}`);
    },
    enabled: selectedIds.length > 0,
    refetchInterval: mode === "live" && !paused ? refreshIntervalSec * 1000 : false,
    refetchOnWindowFocus: false,
    staleTime: 0,
  });

  // Pre-compute how many samples each quality filter option would hide.
  // Surfacing these counts in the QualityFilterSelector dropdown makes
  // it obvious when "Hide bad" and "Good only" would produce identical
  // results (i.e. data has no UNCERTAIN samples in ST 64-127 to differ on).
  const qualityCounts = useMemo(() => {
    if (!historyQuery.data) return undefined;
    const isRaw = historyQuery.data.aggregation === "raw";
    let total = 0, hideBadHidden = 0, goodOnlyHidden = 0;
    for (const s of historyQuery.data.series) {
      for (const p of s.points) {
        total++;
        if (isRaw) {
          // hide_bad drops ST < 64; good_only drops ST < 128.
          // Unknown ST (null) passes hide_bad but fails good_only.
          if (p.st != null && p.st < 64) hideBadHidden++;
          if (p.st == null || p.st < 128) goodOnlyHidden++;
        } else {
          // Aggregated: classified by bucket counts (same rule the chart
          // and tooltip use).
          const bad = p.b ?? 0;
          const good = p.g ?? 0;
          if (bad > 0) {
            hideBadHidden++;
            goodOnlyHidden++;
          } else if (good === 0) {
            // No data in bucket - treated as not-good for good_only.
            goodOnlyHidden++;
          }
        }
      }
    }
    return { total, hideBadHidden, goodOnlyHidden };
  }, [historyQuery.data]);

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
                <>
                  <RefreshIntervalSelector
                    value={refreshIntervalSec}
                    onChange={setRefreshIntervalSec}
                  />
                  <Button
                    variant={paused ? "default" : "outline"}
                    size="sm"
                    className="h-8 text-xs gap-1.5"
                    onClick={() => setPaused((v) => !v)}
                    title={paused
                      ? "Resume live polling"
                      : "Pause polling — chart freezes on the current window"}
                  >
                    {paused
                      ? <><Play className="h-3 w-3" /> Resume</>
                      : <><Pause className="h-3 w-3" /> Pause</>
                    }
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 text-xs gap-1.5"
                    onClick={() => setBufferClearAt(Date.now())}
                    disabled={selectedIds.length === 0}
                    title="Clear the visible live buffer — chart restarts from this moment until the natural window catches up"
                  >
                    <Eraser className="h-3 w-3" />
                    Clear Buffer
                  </Button>
                </>
              )}
              <AggregationSelector
                value={aggregation}
                onChange={setAggregation}
                effective={historyQuery.data?.aggregation}
              />
              <AggregationModeSelector
                value={aggregationMode}
                onChange={setAggregationMode}
                disabled={historyQuery.data?.aggregation === "raw"}
              />
              <QualityFilterSelector
                value={qualityFilter}
                onChange={setQualityFilter}
                counts={qualityCounts}
              />
              <TooltipModeSelector
                value={tooltipMode}
                onChange={setTooltipMode}
              />
              <SavedViews
                currentConfig={currentConfig}
                onLoad={handleLoadView}
              />
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
            maxTags={mode === "live" ? MAX_TAGS_LIVE : MAX_TAGS_HISTORICAL}
          />
        </CardContent>
      </Card>

      {/* ============ Live Value Panel (spec 6.3) ====================
          Renders only when at least one tag is selected. Shows current
          values regardless of historical vs live trend mode - operators
          always see what the plant is doing right now. */}
      <LiveValuePanel
        selectedIds={selectedIds}
        liveMode={mode === "live" && !paused}
        refreshIntervalSec={refreshIntervalSec}
        hiddenTagIds={hiddenTagIds}
        onToggleHidden={toggleHidden}
      />

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
            <TrendChart ref={chartRef} history={historyQuery.data} height={440} tooltipMode={tooltipMode} hiddenTagIds={hiddenTagIds} qualityFilter={qualityFilter} />
          )}
        </CardContent>
      </Card>

      {/* ============ Summary panel ================================
          Uses the actual start/end from the last successful fetch so it
          stays aligned with whatever the chart is currently showing — even
          across pause/resume and mode switches. */}
      {selectedIds.length > 0 && historyQuery.data && (
        <RawDataTable
          selectedIds={selectedIds}
          start={historyQuery.data.start}
          end={historyQuery.data.end}
        />
      )}

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
  const [open, setOpen] = useState(false);
  const [customInput, setCustomInput] = useState<string>(String(value));
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close on outside click — same pattern as TagPicker / SavedViews.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const opt = LIVE_WINDOW_OPTIONS.find((o) => o.minutes === value);
  const label = opt ? opt.label : `Last ${value}m`;

  const applyCustom = () => {
    const n = parseInt(customInput, 10);
    if (Number.isFinite(n) && n >= 1 && n <= 1440) {
      onChange(n);
      setOpen(false);
    }
  };

  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => setOpen((v) => !v)}
      >
        <Clock className="h-3 w-3" />
        {label}
      </Button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-[240px] z-50 bg-card border border-border rounded-md shadow-lg">
          <div className="p-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pb-1">
              Rolling window
            </div>
            <div className="grid grid-cols-2 gap-1">
              {LIVE_WINDOW_OPTIONS.map((o) => (
                <button
                  key={o.minutes}
                  type="button"
                  onClick={() => { onChange(o.minutes); setOpen(false); }}
                  className={`text-left px-3 py-2 rounded text-xs ${value === o.minutes ? "bg-secondary font-medium" : "hover:bg-secondary/40"}`}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <div className="border-t border-border mt-2 pt-2">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pb-1">
                Custom (minutes, 1–1440)
              </div>
              <div className="flex gap-1">
                <Input
                  type="number"
                  min={1}
                  max={1440}
                  value={customInput}
                  onChange={(e) => setCustomInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") applyCustom(); }}
                  className="h-8 text-xs"
                />
                <Button size="sm" className="h-8 px-3 text-xs" onClick={applyCustom}>
                  Apply
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function RefreshIntervalSelector({
  value, onChange,
}: {
  value: number;
  onChange: (sec: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const opt = REFRESH_INTERVAL_OPTIONS.find((o) => o.sec === value);
  const label = opt ? opt.label : `${value}s`;

  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => setOpen((v) => !v)}
        title="Refresh interval — independent of communication polling rate"
      >
        <Timer className="h-3 w-3" />
        Refresh {label}
      </Button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-[160px] z-50 bg-card border border-border rounded-md shadow-lg">
          <div className="p-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pb-1">
              Refresh interval
            </div>
            <div className="grid grid-cols-3 gap-1">
              {REFRESH_INTERVAL_OPTIONS.map((o) => (
                <button
                  key={o.sec}
                  type="button"
                  onClick={() => { onChange(o.sec); setOpen(false); }}
                  className={`px-2 py-1.5 rounded text-xs ${value === o.sec ? "bg-secondary font-medium" : "hover:bg-secondary/40"}`}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
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
