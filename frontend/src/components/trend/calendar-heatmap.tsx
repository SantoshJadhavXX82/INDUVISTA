/**
 * Calendar Heatmap — Phase 23.3 (rebuild).
 *
 * BEFORE: 7×24 grid of (day-of-week × hour-of-day) averages aggregated
 * across N weeks. Looked the same regardless of which window (2w/4w/8w/12w)
 * was selected because more weeks just diluted into the same 168 cells.
 * Operators called it confusing — no dates visible, no investigative
 * utility.
 *
 * NOW: N×24 grid where N = days_count = weeks * 7. Each row is an actual
 * calendar date (e.g. May 23, May 22, ...). Window selector changes the
 * number of rows, so 2w shows 14 rows and 12w shows 84. Restores
 * investigative power ("what happened on May 23 at 06:00?") while keeping
 * the visual pattern detection of the old view (high values still stand
 * out as hot cells).
 *
 * Layout:
 *   - Y axis: dates, newest at top
 *   - X axis: hours of day, 00 → 23 (in the app's configured timezone)
 *   - Cells: average value in that (date, hour) bucket
 *
 * Sizing:
 *   - 2w  (14 rows): row height 22px — comfortable
 *   - 4w  (28 rows): row height 16px — readable, all labels shown
 *   - 8w  (56 rows): row height 10px — every-2nd-day labels
 *   - 12w (84 rows): row height 8px  — every-3rd-day labels, weeks visually separated
 *
 * Color ramp: cool (low value) → hot (high value), 5 stops. Empty cells
 * render as subtle gray so dead hours stand out from data gaps.
 *
 * Dependencies: just lucide-react for the empty-state icon. No new shadcn,
 * no new npm packages, no canvas — pure SVG so hover events work natively.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";
import { BarChart3 } from "lucide-react";


type CalendarCell = {
  date: string;  // ISO YYYY-MM-DD
  hour: number;  // 0-23
  avg: number | null;
  min: number | null;
  max: number | null;
  count: number;
};

type CalendarHeatmapResponse = {
  tag_id: number;
  tag_name: string;
  engineering_unit: string | null;
  data_type: string;
  decimal_places: number | null;   // Phase 23.9 — display precision (NULL = auto)
  weeks: number;
  timezone: string;
  start_date: string;  // ISO YYYY-MM-DD
  end_date: string;    // ISO YYYY-MM-DD
  dates: list_of_strings;  // populated below
  cells: CalendarCell[];
  global_min: number | null;
  global_max: number | null;
  global_avg: number | null;
  total_samples: number;
};
// (TypeScript hack — declared via type so we don't need to expose as global)
type list_of_strings = string[];


const COL_WIDTH_MIN = 38;   // minimum cell width — preserves readability
const COL_WIDTH_MAX = 72;   // cap to keep cells from looking sparse
const LABEL_GUTTER = 64;   // wider for date labels like "May 23"
const TOP_GUTTER = 22;


/** Sequential cool→hot ramp, 5 stops. */
type Stop = { from: number; color: string };

function readRamp(): Stop[] {
  const isDark =
    typeof document !== "undefined" &&
    document.documentElement.getAttribute("data-theme") === "dark";
  if (isDark) {
    return [
      { from: 0.0, color: "#1F3A5F" },
      { from: 0.25, color: "#2F6BB8" },
      { from: 0.5, color: "#5FB87A" },
      { from: 0.75, color: "#E08500" },
      { from: 1.0, color: "#D63031" },
    ];
  }
  return [
    { from: 0.0, color: "#DCEBFF" },
    { from: 0.25, color: "#7EB6F0" },
    { from: 0.5, color: "#7BD888" },
    { from: 0.75, color: "#F5A03A" },
    { from: 1.0, color: "#E0492C" },
  ];
}

function rampColor(t: number, stops: Stop[]): string {
  if (t <= 0) return stops[0].color;
  if (t >= 1) return stops[stops.length - 1].color;
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    if (t >= a.from && t <= b.from) {
      const f = (t - a.from) / (b.from - a.from || 1);
      return f < 0.5 ? a.color : b.color;
    }
  }
  return stops[stops.length - 1].color;
}

function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/**
 * Phase 23.9 — when the tag has a `decimal_places` configured, ALL
 * value labels on the heatmap (subtitle range, color scale, tooltip
 * avg/min/max) honor that fixed precision. Otherwise falls back to
 * the magnitude-tier auto formatter.
 */
function formatValue(v: number | null | undefined, decimalPlaces?: number | null): string {
  if (v == null || !isFinite(v)) return "—";
  if (decimalPlaces != null) {
    const dp = Math.max(0, Math.min(15, decimalPlaces));
    const s = v.toFixed(dp);
    if (Math.abs(v) >= 1000) {
      const [intPart, fracPart] = s.split(".");
      const withSeps = Number(intPart).toLocaleString("en-US");
      return fracPart ? `${withSeps}.${fracPart}` : withSeps;
    }
    return s;
  }
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(3);
}


/**
 * Format an ISO date "2026-05-23" as "23 May" (short, locale-neutral,
 * fits in 56-64px row label gutter).
 */
function formatDateLabel(iso: string): string {
  // Parse YYYY-MM-DD without TZ shenanigans — these are display strings.
  const [y, m, d] = iso.split("-").map((p) => parseInt(p, 10));
  const date = new Date(Date.UTC(y, m - 1, d));
  const monthName = date.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  return `${d} ${monthName}`;
}

/**
 * Format an ISO date for the tooltip — verbose ("Sat, 23 May 2026").
 */
function formatDateTooltip(iso: string): string {
  const [y, m, d] = iso.split("-").map((p) => parseInt(p, 10));
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.toLocaleString("en-US", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}


/**
 * Format the heatmap's date range for the subtitle — compact when both
 * endpoints share a year ("27 Apr → 24 May 2026"), explicit otherwise
 * ("27 Dec 2025 → 24 Jan 2026"). The year anchor on the end label
 * removes any ambiguity for long-window views (12w can reach back
 * 84 days, potentially crossing a year boundary).
 */
function formatDateRangeWithYear(startIso: string, endIso: string): string {
  const parse = (iso: string) => {
    const [y, m, d] = iso.split("-").map((p) => parseInt(p, 10));
    return { y, m, d };
  };
  const s = parse(startIso);
  const e = parse(endIso);
  const monName = (m: number) =>
    new Date(Date.UTC(2000, m - 1, 1)).toLocaleString("en-US", {
      month: "short",
      timeZone: "UTC",
    });
  if (s.y === e.y) {
    return `${s.d} ${monName(s.m)} → ${e.d} ${monName(e.m)} ${e.y}`;
  }
  return `${s.d} ${monName(s.m)} ${s.y} → ${e.d} ${monName(e.m)} ${e.y}`;
}


/**
 * Decide row sizing based on how many days we're rendering.
 * Returns { rowHeight, labelStride } — labelStride is "show every Nth row's label".
 */
function rowSizing(numDays: number): { rowHeight: number; labelStride: number } {
  if (numDays <= 14) return { rowHeight: 22, labelStride: 1 };
  if (numDays <= 28) return { rowHeight: 16, labelStride: 1 };
  if (numDays <= 56) return { rowHeight: 10, labelStride: 2 };
  return { rowHeight: 8, labelStride: 3 };
}


export interface CalendarHeatmapProps {
  tagId: number;
  weeks?: number;
}


export function CalendarHeatmap({ tagId, weeks = 4 }: CalendarHeatmapProps) {
  const query = useQuery({
    queryKey: ["trend", "calendar-heatmap", tagId, weeks],
    queryFn: () =>
      api.get<CalendarHeatmapResponse>(
        `/trends/calendar-heatmap?tag_id=${tagId}&weeks=${weeks}`,
      ),
    staleTime: 60_000,
    retry: 2,
  });

  const [ramp, setRamp] = useState(() => readRamp());
  useEffect(() => {
    const obs = new MutationObserver(() => setRamp(readRamp()));
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => obs.disconnect();
  }, []);

  // Build a (date, hour) → cell lookup. The backend returns only buckets
  // that had samples, so empty cells stay absent from the map (we render
  // them with the empty-color style).
  const cellMap = useMemo(() => {
    const m = new Map<string, CalendarCell>();
    query.data?.cells.forEach((c) => m.set(`${c.date}-${c.hour}`, c));
    return m;
  }, [query.data]);

  // Reverse dates so most-recent renders at TOP (operators investigate
  // recent events more often than old ones).
  const reversedDates = useMemo(() => {
    if (!query.data) return [];
    return [...query.data.dates].reverse();
  }, [query.data]);

  const [hover, setHover] = useState<{
    x: number;
    y: number;
    date: string;
    hour: number;
    cell: CalendarCell | undefined;
  } | null>(null);

  // Phase 23.7 (revised) — CALLBACK REF. See alarm-density-heatmap for
  // full rationale. The previous useRef+useLayoutEffect([]) version
  // failed because this component has early returns for loading/error
  // states before the ref-bearing div, so the ref was null when the
  // effect ran. Callback ref re-fires whenever the live element changes.
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const [availW, setAvailW] = useState(0);
  useEffect(() => {
    if (!container) return;
    setAvailW(container.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setAvailW(w);
    });
    ro.observe(container);
    return () => ro.disconnect();
  }, [container]);

  const colWidth = useMemo(() => {
    if (availW <= 0) return COL_WIDTH_MIN;
    // (avail - label gutter - right padding) / 24 hours
    const computed = (availW - LABEL_GUTTER - 8) / 24;
    return Math.max(COL_WIDTH_MIN, Math.min(COL_WIDTH_MAX, computed));
  }, [availW]);

  if (query.isLoading) {
    return (
      <div
        className="text-sm text-center py-8"
        style={{ color: "var(--text-secondary)" }}
      >
        Loading calendar heatmap…
      </div>
    );
  }
  if (query.isError) {
    const msg =
      query.error instanceof Error ? query.error.message : String(query.error);
    return (
      <div
        className="text-sm rounded-md px-3 py-3 flex items-start gap-3"
        style={{
          color: "var(--status-error-on-soft)",
          backgroundColor: "var(--status-error-soft)",
        }}
      >
        <div className="flex-1">
          <div className="font-medium">Couldn't load calendar heatmap</div>
          <div className="text-[11px] mt-0.5 opacity-80">{msg}</div>
        </div>
        <button
          type="button"
          onClick={() => query.refetch()}
          className="text-[11px] font-medium rounded px-2 py-1"
          style={{
            backgroundColor: "var(--bg-elevated)",
            color: "var(--text-primary)",
          }}
        >
          Retry
        </button>
      </div>
    );
  }

  const data = query.data!;

  const gMin = data.global_min ?? 0;
  const gMax = data.global_max ?? 0;
  const span = Math.max(1e-9, gMax - gMin);

  const numDays = reversedDates.length;
  const { rowHeight, labelStride } = rowSizing(numDays);

  // SVG dimensions (use responsive colWidth from useLayoutEffect above)
  const gridW = colWidth * 24;
  const gridH = rowHeight * numDays;
  const svgW = LABEL_GUTTER + gridW + 8;
  const svgH = TOP_GUTTER + gridH + 8;

  const emptyColor = cssVar("--ios-gray-5", "#E5E5EA");
  const sevenDayLineColor = cssVar("--separator", "rgba(0,0,0,0.08)");

  // Show the no-data state inside the layout (instead of replacing it)
  // so operators can still see WHICH days had no data.
  const hasAnyData = data.total_samples > 0;

  // Year is shown unambiguously in the subtitle — uses the end_date's
  // year (and both if the window crosses a year boundary).
  const subtitleRange = formatDateRangeWithYear(data.start_date, data.end_date);

  return (
    <div ref={setContainer} className="relative">
      {/* Heading line */}
      <div className="flex items-baseline gap-2 mb-2 flex-wrap">
        <div
          className="text-[13px] font-medium"
          style={{ color: "var(--text-primary)" }}
        >
          {data.tag_name}
        </div>
        {data.engineering_unit && (
          <span
            className="text-[11px]"
            style={{ color: "var(--text-secondary)" }}
          >
            ({data.engineering_unit})
          </span>
        )}
        <div
          className="text-[11px] tabular-nums ml-auto"
          style={{ color: "var(--text-secondary)" }}
        >
          {subtitleRange} ·{" "}
          {data.total_samples.toLocaleString()} samples · range{" "}
          {formatValue(gMin, data.decimal_places)}–{formatValue(gMax, data.decimal_places)} · {data.timezone}
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "center" }}>
        <svg
          width={svgW}
          height={svgH}
          style={{ display: "block", maxWidth: "100%" }}
        >
          {/* Hour-of-day column labels (every 2 hours, top gutter) */}
          {Array.from({ length: 24 }, (_, h) => h)
            .filter((h) => h % 2 === 0)
            .map((h) => (
              <text
                key={`hh${h}`}
                x={LABEL_GUTTER + h * colWidth + colWidth / 2}
                y={TOP_GUTTER - 6}
                textAnchor="middle"
                style={{
                  fontSize: 10,
                  fill: "var(--text-secondary)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {h.toString().padStart(2, "0")}
              </text>
            ))}

          {/* Date row labels (left gutter) */}
          {reversedDates.map((iso, rowIdx) => {
            const showLabel = rowIdx % labelStride === 0;
            return showLabel ? (
              <text
                key={`d${iso}`}
                x={LABEL_GUTTER - 8}
                y={TOP_GUTTER + rowIdx * rowHeight + rowHeight / 2 + 3}
                textAnchor="end"
                style={{
                  fontSize: Math.min(11, Math.max(8, rowHeight - 1)),
                  fill: "var(--text-secondary)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {formatDateLabel(iso)}
              </text>
            ) : null;
          })}

          {/* Subtle horizontal week-boundary lines for the dense views.
              Skipped on 14-row view since rows are already roomy. */}
          {numDays > 14 &&
            reversedDates.map((iso, rowIdx) => {
              // Compute the local weekday from the ISO date to find
              // week boundaries. Show a separator BEFORE every Sunday
              // (=isoweekday 7), i.e. above the Sunday row when newest
              // is at top.
              const [y, m, d] = iso.split("-").map((p) => parseInt(p, 10));
              const date = new Date(Date.UTC(y, m - 1, d));
              const dow = date.getUTCDay(); // 0=Sun ... 6=Sat
              if (dow !== 1) return null; // line above Monday for visual rhythm
              const lineY = TOP_GUTTER + rowIdx * rowHeight;
              return (
                <line
                  key={`wk${iso}`}
                  x1={LABEL_GUTTER}
                  y1={lineY}
                  x2={LABEL_GUTTER + gridW}
                  y2={lineY}
                  stroke={sevenDayLineColor}
                  strokeWidth={0.5}
                />
              );
            })}

          {/* Cells */}
          {reversedDates.map((iso, rowIdx) =>
            Array.from({ length: 24 }, (_, hour) => {
              const cell = cellMap.get(`${iso}-${hour}`);
              const hasData = !!cell && cell.count > 0 && cell.avg != null;
              const t = hasData ? (cell!.avg! - gMin) / span : 0;
              const fill = hasData ? rampColor(t, ramp) : emptyColor;
              const gap = rowHeight >= 16 ? 1.5 : 0.5;
              const cellRadius = rowHeight >= 14 ? 3 : 1.5;
              const x = LABEL_GUTTER + hour * colWidth + gap;
              const y = TOP_GUTTER + rowIdx * rowHeight + gap;
              const w = colWidth - gap * 2;
              const h = rowHeight - gap * 2;

              return (
                <rect
                  key={`c-${iso}-${hour}`}
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  rx={cellRadius}
                  ry={cellRadius}
                  fill={fill}
                  style={{ cursor: "crosshair" }}
                  onMouseEnter={(e) => {
                    const rect = (
                      e.target as SVGRectElement
                    ).getBoundingClientRect();
                    setHover({
                      x: rect.left + rect.width / 2,
                      y: rect.top,
                      date: iso,
                      hour,
                      cell,
                    });
                  }}
                  onMouseLeave={() => setHover(null)}
                />
              );
            }),
          )}
        </svg>
      </div>

      {!hasAnyData && (
        <div
          className="text-xs text-center mt-2"
          style={{ color: "var(--text-secondary)" }}
        >
          No samples for this tag in the selected window.
        </div>
      )}

      {/* Color-scale legend */}
      {hasAnyData && (
        <div
          className="flex items-center justify-center gap-2 mt-2 text-[11px] tabular-nums"
          style={{ color: "var(--text-secondary)" }}
        >
          <span>{formatValue(gMin, data.decimal_places)}</span>
          <span
            style={{
              display: "inline-block",
              width: 140,
              height: 10,
              background: `linear-gradient(to right, ${ramp
                .map((s) => s.color)
                .join(", ")})`,
              borderRadius: 3,
              border: "0.5px solid var(--separator)",
            }}
          />
          <span>
            {formatValue(gMax, data.decimal_places)} {data.engineering_unit ?? ""}
          </span>
        </div>
      )}

      {/* Tooltip */}
      {hover && (
        <div
          className="fixed z-50 pointer-events-none rounded-lg px-3 py-2 text-[11px]"
          style={{
            left: hover.x + 12,
            top: hover.y - 10,
            transform: "translateY(-100%)",
            backgroundColor: "var(--bg-elevated)",
            color: "var(--text-primary)",
            border: "0.5px solid var(--card-edge)",
            boxShadow: "0 4px 16px rgba(0,0,0,0.18)",
            maxWidth: 260,
          }}
        >
          <div className="font-medium">
            {formatDateTooltip(hover.date)} ·{" "}
            {hover.hour.toString().padStart(2, "0")}:00 –{" "}
            {((hover.hour + 1) % 24).toString().padStart(2, "0")}:00
          </div>
          {hover.cell && hover.cell.count > 0 ? (
            <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 tabular-nums">
              <span style={{ color: "var(--text-secondary)" }}>Avg</span>
              <span className="font-medium">
                {formatValue(hover.cell.avg, data.decimal_places)} {data.engineering_unit ?? ""}
              </span>
              <span style={{ color: "var(--text-secondary)" }}>Min</span>
              <span>{formatValue(hover.cell.min, data.decimal_places)}</span>
              <span style={{ color: "var(--text-secondary)" }}>Max</span>
              <span>{formatValue(hover.cell.max, data.decimal_places)}</span>
              <span style={{ color: "var(--text-secondary)" }}>Samples</span>
              <span>{hover.cell.count.toLocaleString()}</span>
            </div>
          ) : (
            <div className="mt-1.5" style={{ color: "var(--text-secondary)" }}>
              No samples in this bucket
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Card wrapper — own tag picker + weeks picker.
// ---------------------------------------------------------------------------

interface TagOption {
  id: number;
  name: string;
  engineering_unit: string | null;
}

export interface CalendarHeatmapCardProps {
  initialTagId?: number;
}

export function CalendarHeatmapCard({ initialTagId }: CalendarHeatmapCardProps) {
  const [tagId, setTagId] = useState<number | null>(initialTagId ?? null);
  const [weeks, setWeeks] = useState<number>(4);

  const [overrode, setOverrode] = useState(false);
  useEffect(() => {
    if (!overrode && initialTagId != null) setTagId(initialTagId);
  }, [initialTagId, overrode]);

  const tagsQuery = useQuery({
    queryKey: ["trend-tags-min"],
    queryFn: () => api.get<TagOption[]>("/trends/tags?limit=2000"),
    staleTime: 5 * 60_000,
  });

  return (
    <SectionCard
      title="Calendar heatmap"
      subtitle="Each row is a calendar date, each column an hour. Hot cells = high values. Pick a window to control how many days are shown."
      action={
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={tagId ?? ""}
            onChange={(e) => {
              setTagId(e.target.value ? Number(e.target.value) : null);
              setOverrode(true);
            }}
            className="text-[11px] rounded-md px-2 py-1 border outline-none"
            style={{
              backgroundColor: "var(--bg-elevated)",
              color: "var(--text-primary)",
              borderColor: "var(--separator)",
              maxWidth: 220,
            }}
          >
            <option value="">— pick a tag —</option>
            {tagsQuery.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.engineering_unit ? ` (${t.engineering_unit})` : ""}
              </option>
            ))}
          </select>

          <div
            className="flex gap-0.5 p-0.5 rounded-md"
            style={{ backgroundColor: "var(--ios-gray-5)" }}
          >
            {[2, 4, 8, 12].map((w) => (
              <button
                key={w}
                type="button"
                onClick={() => setWeeks(w)}
                className="text-[11px] font-medium rounded px-2 py-0.5 transition-colors"
                style={
                  weeks === w
                    ? {
                        backgroundColor: "var(--bg-elevated)",
                        color: "var(--text-primary)",
                      }
                    : { color: "var(--text-secondary)" }
                }
              >
                {w}w
              </button>
            ))}
          </div>
        </div>
      }
    >
      {tagId == null ? (
        <div
          className="text-sm text-center py-10 flex flex-col items-center gap-2"
          style={{ color: "var(--text-secondary)" }}
        >
          <BarChart3 style={{ width: 32, height: 32, opacity: 0.4 }} />
          <div>Pick a tag from the dropdown above to see its daily pattern.</div>
        </div>
      ) : (
        <CalendarHeatmap tagId={tagId} weeks={weeks} />
      )}
    </SectionCard>
  );
}
