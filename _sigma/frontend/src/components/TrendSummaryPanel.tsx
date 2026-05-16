/**
 * Phase 13.3a — Trend summary panel.
 *
 * Fetches /api/trends/summary for the same tag_ids + time range the chart
 * is showing, and renders per-tag availability + quality counts. Maps
 * directly to spec §10.3.
 *
 * The panel is operator-scannable: one row per tag, color-graded
 * availability badge, bad/missing counts highlighted when non-zero.
 * Hover any cell for full detail (first/last sample timestamps,
 * expected vs actual, longest gap exact time).
 */
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { TrendSummaryResponse } from "@/types/api";
import { useTimeFormat } from "@/lib/timeFormat";
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
};

export default function TrendSummaryPanel({
  tagIds, start, end,
}: TrendSummaryPanelProps) {
  const { formatDateTime } = useTimeFormat();

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
  });

  if (tagIds.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center justify-between">
          <span>Data quality &amp; availability</span>
          {summaryQuery.isFetching && (
            <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
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
                <TableHead>Longest gap</TableHead>
                <TableHead>First sample</TableHead>
                <TableHead>Last sample</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {summaryQuery.data.tags.map((s, idx) => {
                // Color order matches the chart's series order, which
                // matches the order tagIds is passed in. summary endpoint
                // sorts by tag_name though — so we look up the index by
                // searching tagIds.
                const tagIdx = tagIds.findIndex((id) => id === s.tag_id);
                const color = TAG_COLOR_AT(tagIdx >= 0 ? tagIdx : idx);

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
                    <TableCell
                      className="text-right tabular-nums whitespace-nowrap"
                    >
                      <SigmaInfoPopover
                        mean={s.mean_value}
                        stddev={s.stddev_value}
                        observedMin={s.observed_min}
                        observedMax={s.observed_max}
                        unit={s.engineering_unit}
                        tagName={s.tag_name}
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
