/**
 * Phase 13.3a — Trend summary panel.
 *
 * Fetches /api/trends/summary for the same tag_ids + time range the chart
 * is showing, and renders per-tag availability + quality counts. Maps
 * directly to spec §10.3.
 *
 * Phase 13.12 — ROC column + unit selector:
 *   The summary endpoint returns aggregates only (mean / σ / min / max),
 *   so a separate, narrow /trends/history fetch retrieves the trailing
 *   5 min of raw samples per tag and feeds computeROC().
 *
 * Phase 13.12b — keepPreviousData on both queries:
 *   In live mode, Trend.tsx passes historyQuery.data.end which advances
 *   each scan. That used to change our query keys, briefly nulling
 *   `data`, unmounting the Table, and dropping the pinned state of any
 *   SigmaInfoPopover the operator was inspecting. `placeholderData:
 *   keepPreviousData` preserves the previous response while a new fetch
 *   is in flight, so the Table never unmounts and popover state
 *   survives across scans.
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { AlertTriangle, RefreshCw, HelpCircle, ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/lib/api";
import type {
  TrendSummaryResponse,
  TrendHistoryResponse,
} from "@/types/api";
import { useTimeFormat } from "@/lib/timeFormat";
import {
  computeROC,
  formatROC,
  loadRocUnit,
  rocTooltip,
  saveRocUnit,
  type RocSample,
  type RocUnit,
} from "@/lib/trendRoc";
import RocUnitSelector from "@/components/RocUnitSelector";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import SigmaInfoPopover from "@/components/SigmaInfoPopover";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { TAG_COLOR_AT } from "@/components/TagPicker";

type TrendSummaryPanelProps = {
  tagIds: number[];
  start: string;       // ISO UTC
  end: string;         // ISO UTC
  /** Forwarded to SigmaInfoPopover on each row's σ cell so the operator
      can click through from the bell-curve tooltip to the raw data
      table filtered to that tag. */
  onShowInRawTable?: (tagId: number) => void;
  /**
   * Phase 13.13 — ROC visibility.
   *
   * ROC (rate of change) is only meaningful at the trailing edge of a
   * live data feed: it asks "how fast is the value changing RIGHT NOW".
   * In a historical view the end-of-range edge is frozen and "now" has
   * already passed, so the value would be misleading (or zero, for old
   * ranges). Pass `false` when the chart is in historical mode to hide
   * the ROC column, the ROC unit selector, and skip the ROC history
   * fetch entirely.
   */
  showRoc?: boolean;
};

// Trailing window for ROC. 5 min of raw data covers the least-squares
// fit (capped at 20 samples in trendRoc) while staying cheap.
const ROC_WINDOW_MS = 5 * 60 * 1000;

export default function TrendSummaryPanel({
  tagIds, start, end, onShowInRawTable, showRoc = true,
}: TrendSummaryPanelProps) {
  const { formatDateTime } = useTimeFormat();

  // Help panel disclosure state (sticky preference would be nice but
  // not warranted yet — operators rarely re-open the same chart).
  const [helpOpen, setHelpOpen] = useState(false);

  // ROC unit state, persisted to localStorage.
  const [rocUnit, setRocUnit] = useState<RocUnit>(() => loadRocUnit());
  const handleRocUnitChange = (u: RocUnit) => {
    setRocUnit(u);
    saveRocUnit(u);
  };

  const summaryQuery = useQuery({
    queryKey: ["trend-summary", tagIds, start, end],
    queryFn: () => {
      const params = new URLSearchParams({
        tag_ids: tagIds.join(","),
        start, end,
      });
      return api.get<TrendSummaryResponse>(`/trends/summary?${params}`);
    },
    enabled: tagIds.length > 0,
    staleTime: 0,
    // Keep the previous response visible while a new fetch is in flight.
    // Without this, every chart refetch in live mode briefly nulls
    // `data` and unmounts the Table, which would tear down any
    // SigmaInfoPopover the operator had pinned.
    placeholderData: keepPreviousData,
  });

  // Trailing 5-minute raw fetch dedicated to ROC. Independent of the
  // chart's own (potentially larger or aggregated) history query.
  const rocWindow = useMemo(() => {
    if (tagIds.length === 0) return null;
    const endMs = new Date(end).getTime();
    if (!isFinite(endMs)) return null;
    return {
      start: new Date(endMs - ROC_WINDOW_MS).toISOString(),
      end,
    };
  }, [end, tagIds.length]);

  const rocHistoryQuery = useQuery({
    queryKey: [
      "trend-roc-history", tagIds, rocWindow?.start, rocWindow?.end,
    ],
    queryFn: () => {
      const params = new URLSearchParams({
        tag_ids: tagIds.join(","),
        start: rocWindow!.start,
        end: rocWindow!.end,
        aggregation: "raw",
        max_points: "2000",
      });
      return api.get<TrendHistoryResponse>(`/trends/history?${params}`);
    },
    enabled: showRoc && tagIds.length > 0 && rocWindow != null,
    staleTime: 0,
    // Same rationale as summaryQuery — keep the last ROC dataset
    // visible across scans so the cell doesn't flicker to "—".
    placeholderData: keepPreviousData,
  });

  // Index series by tag_id and convert TrendPoint -> RocSample once
  // per response. Null values skipped; ST byte forwarded as q so the
  // quality filter inside computeROC works (>= 128 = GOOD). Defensive
  // against unordered responses — computeROC sorts internally.
  const rocSeriesByTag = useMemo(() => {
    const map = new Map<number, RocSample[]>();
    rocHistoryQuery.data?.series.forEach((s) => {
      const samples: RocSample[] = [];
      for (const p of s.points) {
        if (p.v == null) continue;
        const tMs = new Date(p.t).getTime();
        if (!isFinite(tMs)) continue;
        samples.push({ t: tMs, v: p.v, q: p.st });
      }
      map.set(s.tag_id, samples);
    });
    return map;
  }, [rocHistoryQuery.data]);

  if (tagIds.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center justify-between gap-3">
          <span className="flex items-center gap-2">
            Data quality &amp; availability
            <button
              type="button"
              onClick={() => setHelpOpen((v) => !v)}
              className="inline-flex items-center gap-1 text-xs font-normal text-muted-foreground hover:text-foreground"
              title={helpOpen ? "Hide column reference" : "Show column reference"}
            >
              <HelpCircle className="h-3.5 w-3.5" />
              {helpOpen ? (
                <ChevronUp className="h-3 w-3" />
              ) : (
                <ChevronDown className="h-3 w-3" />
              )}
            </button>
          </span>
          <div className="flex items-center gap-3">
            {showRoc && (
              <RocUnitSelector value={rocUnit} onChange={handleRocUnitChange} />
            )}
            {(summaryQuery.isFetching ||
              (showRoc && rocHistoryQuery.isFetching)) && (
              <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />
            )}
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {helpOpen && (
          <HelpPanel showRoc={showRoc} onClose={() => setHelpOpen(false)} />
        )}

        {summaryQuery.isLoading && (
          <p className="text-xs text-muted-foreground py-2">Loading summary…</p>
        )}

        {summaryQuery.isError && (
          <div className="flex items-start gap-2 text-xs text-destructive py-2">
            <AlertTriangle className="h-4 w-4 flex-shrink-0" />
            <span>
              Failed to load summary:{" "}
              {(summaryQuery.error as Error)?.message}
            </span>
          </div>
        )}

        {rocHistoryQuery.isError && (
          <div className="flex items-start gap-2 text-xs text-amber-700 py-1">
            <AlertTriangle className="h-4 w-4 flex-shrink-0" />
            <span>
              ROC fetch failed: {(rocHistoryQuery.error as Error)?.message}
            </span>
          </div>
        )}

        {summaryQuery.data && summaryQuery.data.tags.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tag</TableHead>
                <TableHead className="text-right">Availability</TableHead>
                <TableHead className="text-right">Good %</TableHead>
                <TableHead className="text-right">Good</TableHead>
                <TableHead className="text-right">Uncertain</TableHead>
                <TableHead className="text-right">Bad</TableHead>
                <TableHead className="text-right">Missing</TableHead>
                <TableHead className="text-right" title="Arithmetic mean of GOOD samples">Mean</TableHead>
                <TableHead className="text-right normal-case" title="Sample standard deviation (population n-1) of GOOD samples">
                  <span className="italic">σ</span> (STD DEV)
                </TableHead>
                <TableHead className="text-right" title="Min – Max observed across GOOD samples">Range</TableHead>
                {showRoc && (
                  <TableHead
                    className="text-right"
                    title="Rate of change at the trailing edge — least-squares slope over the last 5 min of GOOD samples (ST >= 128, max 20). Hover any cell for diagnostic detail."
                  >
                    ROC
                  </TableHead>
                )}
                <TableHead>Longest gap</TableHead>
                <TableHead>First sample</TableHead>
                <TableHead>Last sample</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {summaryQuery.data.tags.map((s, idx) => {
                const tagIdx = tagIds.findIndex((id) => id === s.tag_id);
                const color = TAG_COLOR_AT(tagIdx >= 0 ? tagIdx : idx);

                const rocSamples = rocSeriesByTag.get(s.tag_id) ?? [];
                const roc = computeROC(rocSamples, rocUnit);

                return (
                  <TableRow key={s.tag_id}>
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-2">
                        <span
                          className="inline-block w-3 h-3 rounded-sm flex-shrink-0"
                          style={{ backgroundColor: color }}
                          aria-hidden
                        />
                        <span>{s.tag_name}</span>
                      </div>
                    </TableCell>
                    <TableCell
                      className="text-right tabular-nums"
                      title={`${s.actual_samples} of ${s.expected_samples} expected`}
                    >
                      <Badge
                        variant="outline"
                        className={availabilityClass(s.availability_pct)}
                      >
                        {s.availability_pct.toFixed(1)}%
                      </Badge>
                    </TableCell>
                    <TableCell
                      className="text-right tabular-nums"
                      title={`${s.good_samples} good of ${s.expected_samples} expected`}
                    >
                      <span className={qualityPctClass(s.good_availability_pct)}>
                        {s.good_availability_pct.toFixed(1)}%
                      </span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-emerald-700">
                      {s.good_samples.toLocaleString()}
                    </TableCell>
                    <TableCell
                      className={`text-right tabular-nums ${s.uncertain_samples > 0 ? "text-amber-700" : "text-muted-foreground"}`}
                    >
                      {s.uncertain_samples.toLocaleString()}
                    </TableCell>
                    <TableCell
                      className={`text-right tabular-nums ${badCountClass(s.bad_samples)}`}
                    >
                      {s.bad_samples.toLocaleString()}
                    </TableCell>
                    <TableCell
                      className={`text-right tabular-nums ${s.missing_samples > 0 ? "text-amber-700" : "text-muted-foreground"}`}
                    >
                      {s.missing_samples.toLocaleString()}
                    </TableCell>
                    <TableCell
                      className="text-right tabular-nums whitespace-nowrap"
                      title={s.mean_value != null && s.engineering_unit
                        ? `${s.mean_value} ${s.engineering_unit}`
                        : undefined}
                    >
                      {formatStat(s.mean_value)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums whitespace-nowrap">
                      <SigmaInfoPopover
                        tagId={s.tag_id}
                        mean={s.mean_value}
                        stddev={s.stddev_value}
                        observedMin={s.observed_min}
                        observedMax={s.observed_max}
                        unit={s.engineering_unit}
                        tagName={s.tag_name}
                        onShowInRawTable={onShowInRawTable}
                      >
                        {formatStat(s.stddev_value)}
                      </SigmaInfoPopover>
                    </TableCell>
                    <TableCell
                      className="text-right tabular-nums whitespace-nowrap text-muted-foreground"
                      title={s.observed_min != null && s.observed_max != null
                        ? `Min ${s.observed_min} - Max ${s.observed_max}${s.engineering_unit ? " " + s.engineering_unit : ""}`
                        : undefined}
                    >
                      {s.observed_min != null && s.observed_max != null
                        ? `${formatStat(s.observed_min)} – ${formatStat(s.observed_max)}`
                        : "—"}
                    </TableCell>
                    {showRoc && (
                      <TableCell
                        className="text-right tabular-nums whitespace-nowrap text-slate-700"
                        data-roc-cell={s.tag_id}
                        title={rocTooltip(roc)}
                      >
                        {formatROC(roc, s.engineering_unit)}
                      </TableCell>
                    )}
                    <TableCell className="text-xs">
                      {s.longest_gap_sec != null
                        ? (
                            <span
                              title={
                                s.longest_gap_start
                                  ? `Starting at ${formatDateTime(s.longest_gap_start)}`
                                  : undefined
                              }
                            >
                              {formatDuration(s.longest_gap_sec)}
                            </span>
                          )
                        : <span className="text-muted-foreground">—</span>
                      }
                    </TableCell>
                    <TableCell className="text-xs tabular-nums whitespace-nowrap">
                      {s.first_sample
                        ? <span title={`UTC ${s.first_sample}`}>{formatDateTime(s.first_sample)}</span>
                        : <span className="text-muted-foreground">—</span>}
                    </TableCell>
                    <TableCell className="text-xs tabular-nums whitespace-nowrap">
                      {s.last_sample
                        ? <span title={`UTC ${s.last_sample}`}>{formatDateTime(s.last_sample)}</span>
                        : <span className="text-muted-foreground">—</span>}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}

        {summaryQuery.data && summaryQuery.data.tags.length === 0 && (
          <p className="text-xs text-muted-foreground py-2">
            No summary data available for the selected window.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Color thresholds — same scheme as the worker status badges so operators
// learn one visual language across the app.
// ---------------------------------------------------------------------------

function availabilityClass(pct: number): string {
  if (pct >= 95) return "border-emerald-300 text-emerald-800 bg-emerald-50";
  if (pct >= 80) return "border-blue-300 text-blue-800 bg-blue-50";
  if (pct >= 50) return "border-amber-300 text-amber-800 bg-amber-50";
  return "border-red-300 text-red-800 bg-red-50";
}

function qualityPctClass(pct: number): string {
  if (pct >= 95) return "text-emerald-700 font-medium";
  if (pct >= 80) return "text-foreground";
  if (pct >= 50) return "text-amber-700";
  return "text-red-700 font-medium";
}

function badCountClass(n: number): string {
  if (n === 0) return "text-muted-foreground";
  if (n < 50) return "text-amber-700";
  return "text-red-700 font-semibold";
}

function formatDuration(sec: number): string {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return s === 0 ? `${m}m` : `${m}m ${s}s`;
  }
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

/**
 * Compact numeric formatting for the mean / stddev / min / max cells.
 * Tight precision so columns don't blow out: decimals tier on magnitude,
 * trailing zeros trimmed. Null renders as an em dash to match the visual
 * convention used elsewhere in the panel.
 */
function formatStat(v: number | null): string {
  if (v == null || isNaN(v)) return "—";
  const abs = Math.abs(v);
  let str: string;
  if (abs >= 1000)     str = v.toFixed(1);
  else if (abs >= 100) str = v.toFixed(2);
  else if (abs >= 1)   str = v.toFixed(3);
  else                  str = v.toFixed(4);
  return str.replace(/\.?0+$/, "");
}

// ---------------------------------------------------------------------------
// Help panel
// ---------------------------------------------------------------------------

/**
 * Inline reference for every column in the summary table.
 *
 * Definitions and time windows are stated explicitly. Operators
 * sometimes care a lot whether Mean is "across the whole visible range"
 * vs "trailing N minutes" — making this obvious avoids the wrong
 * conclusion. ROC is the only field with its own window (5 min); all
 * other statistical fields use the visible time range.
 */
function HelpPanel({
  showRoc, onClose,
}: {
  showRoc: boolean;
  onClose: () => void;
}) {
  return (
    <div className="mb-3 rounded-md border border-blue-200 bg-blue-50/40 p-3 text-xs">
      <div className="flex items-center justify-between mb-2">
        <span className="font-medium text-blue-900">Column reference</span>
        <button
          type="button"
          onClick={onClose}
          className="text-blue-700 hover:text-blue-900 text-[11px]"
        >
          Hide
        </button>
      </div>

      <div className="text-blue-900/80 mb-3">
        Unless noted, every statistic is computed across the
        <strong> currently visible time range </strong>
        (the start and end of the chart). Change the range or live
        window and the values recompute.
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-blue-900/90">
        <HelpRow term="Availability">
          Percentage of expected samples that actually landed in the
          database, regardless of quality. <code>actual / expected × 100</code>.
          Expected = (range duration) / (tag scan interval).
        </HelpRow>

        <HelpRow term="Good %">
          Percentage of expected samples that landed
          <strong> and </strong> were quality GOOD (ST ≥ 128).
          A high Availability with low Good % means data is flowing
          but a sensor or transport is flagging UNCERTAIN / BAD.
        </HelpRow>

        <HelpRow term="Good">
          Count of samples with ST ≥ 128. These are the only samples
          included in Mean / σ / Range / ROC.
        </HelpRow>

        <HelpRow term="Uncertain">
          Count of samples with ST in [64, 127]. Typically: stale value,
          sensor drift, low confidence. <strong>Excluded</strong> from statistics.
        </HelpRow>

        <HelpRow term="Bad">
          Count of samples with ST &lt; 64. Typically: comm fault, sensor
          fault, out of range. <strong>Excluded</strong> from statistics.
        </HelpRow>

        <HelpRow term="Missing">
          Expected samples that never arrived (no row at all in the database).
          <code> expected − actual</code>. High missing usually means
          modbus worker downtime or a network gap.
        </HelpRow>

        <HelpRow term="Mean">
          Arithmetic mean of GOOD samples, computed over the visible
          time range. <code>Σv / N_good</code>. Non-GOOD samples are skipped.
        </HelpRow>

        <HelpRow term="σ (STD DEV)">
          Sample standard deviation of GOOD samples over the visible
          time range, using Bessel's correction (divisor <code>n − 1</code>).
          Click any σ cell for the bell-curve breakdown and a jump-to-raw
          link.
        </HelpRow>

        <HelpRow term="Range">
          Minimum and maximum GOOD values observed in the visible time
          range. Outlier-sensitive — a single spike will widen Range.
        </HelpRow>

        {showRoc && (
          <HelpRow term="ROC">
            Rate of change at the trailing edge of the data — least-squares
            slope over the <strong>last 5 minutes</strong> of GOOD samples
            (max 20 points). Independent of the visible range; always uses
            the trailing 5 min from the chart's end time. The unit selector
            (/s, /min, /hr) only affects display. Only shown in
            <strong> Real-Time </strong>mode — in Historical mode the
            trailing edge isn't "now", so the value would be misleading.
          </HelpRow>
        )}

        <HelpRow term="Longest gap">
          Longest stretch of missing-or-non-GOOD samples within the visible
          range, in seconds (with a tooltip showing when it started).
          Useful for finding when a sensor or worker dropped out.
        </HelpRow>

        <HelpRow term="First / Last sample">
          Timestamps of the earliest and latest sample of <em>any</em>
          quality within the visible range. Helps confirm the actual
          coverage when a tag was enabled mid-window.
        </HelpRow>
      </div>
    </div>
  );
}

function HelpRow({
  term, children,
}: { term: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="font-medium">{term}</div>
      <div className="text-[11px] leading-snug">{children}</div>
    </div>
  );
}
