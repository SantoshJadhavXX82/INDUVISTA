/**
 * Phase 23.3 — CalendarHeatmap.
 *
 * Aggregates one tag's values across the last N weeks into a 7×24 grid:
 *   - rows  = day-of-week (Mon → Sun, ISO ordering)
 *   - cols  = hour-of-day (00 → 23)
 *   - color = average value, normalized to the tag's observed [min, max]
 *
 * Why a separate panel from the line chart on Trend:
 *   - The chart shows "what happened over time"
 *   - The calendar shows "what time does it tend to happen"
 *   - Together they answer different questions
 *
 * SVG vs canvas: 168 cells, no perf concern, and SVG lets the hover
 * tooltip use native pointer events without canvas hit-testing math.
 *
 * Color choices: a sequential ramp from cool (low value) → hot (high
 * value). For tags with mostly-stable readings the whole grid will look
 * one color, with the few off-pattern hours standing out. Empty cells
 * (no samples that hour) show as a subtle gray tile.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";
import { BarChart3 } from "lucide-react";


type CalendarCell = {
  dow: number;   // 1=Mon ... 7=Sun
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
  weeks: number;
  cells: CalendarCell[];
  global_min: number | null;
  global_max: number | null;
  global_avg: number | null;
  total_samples: number;
};


const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const ROW_HEIGHT = 28;
const COL_WIDTH = 38;
const LABEL_GUTTER = 44;  // left gutter for "Mon Tue Wed…"
const TOP_GUTTER = 22;    // top gutter for "00 02 04…"


/** Sequential cool→hot ramp with 6 stops. */
type Stop = { from: number; color: string };

function readRamp(): Stop[] {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  if (isDark) {
    return [
      { from: 0.00, color: "#1F3A5F" },   // deep blue
      { from: 0.25, color: "#2F6BB8" },   // mid blue
      { from: 0.50, color: "#5FB87A" },   // green
      { from: 0.75, color: "#E08500" },   // amber
      { from: 1.00, color: "#D63031" },   // red
    ];
  }
  return [
    { from: 0.00, color: "#DCEBFF" },   // pale blue
    { from: 0.25, color: "#7EB6F0" },
    { from: 0.50, color: "#7BD888" },
    { from: 0.75, color: "#F5A03A" },
    { from: 1.00, color: "#E0492C" },
  ];
}

function rampColor(t: number, stops: Stop[]): string {
  if (t <= 0) return stops[0].color;
  if (t >= 1) return stops[stops.length - 1].color;
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    if (t >= a.from && t <= b.from) {
      // Snap to the closer color rather than interpolating in hex space
      // (proper RGB lerp would be nicer but adds code; the snap reads
      // cleanly as "tiered intensity" instead of muddy in-betweens).
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


function formatValue(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 1)   return v.toFixed(2);
  return v.toFixed(3);
}


export interface CalendarHeatmapProps {
  tagId: number;
  weeks?: number;
}


export function CalendarHeatmap({ tagId, weeks = 4 }: CalendarHeatmapProps) {
  const query = useQuery({
    queryKey: ["trend", "calendar-heatmap", tagId, weeks],
    queryFn: () => api.get<CalendarHeatmapResponse>(
      `/trends/calendar-heatmap?tag_id=${tagId}&weeks=${weeks}`
    ),
    staleTime: 60_000,
    retry: 2,
  });

  const [ramp, setRamp] = useState(() => readRamp());
  useEffect(() => {
    const obs = new MutationObserver(() => setRamp(readRamp()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  // Build a 7×24 cell-lookup so we can render the full grid even when
  // some (dow, hour) pairs had zero samples.
  const cellMap = useMemo(() => {
    const m = new Map<string, CalendarCell>();
    query.data?.cells.forEach((c) => m.set(`${c.dow}-${c.hour}`, c));
    return m;
  }, [query.data]);

  const [hover, setHover] = useState<{
    x: number; y: number; dow: number; hour: number; cell: CalendarCell | undefined;
  } | null>(null);

  if (query.isLoading) {
    return (
      <div className="text-sm text-center py-8" style={{ color: "var(--text-secondary)" }}>
        Loading calendar heatmap…
      </div>
    );
  }
  if (query.isError) {
    const msg = query.error instanceof Error ? query.error.message : String(query.error);
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
          style={{ backgroundColor: "var(--bg-elevated)", color: "var(--text-primary)" }}
        >Retry</button>
      </div>
    );
  }

  const data = query.data!;
  if (data.total_samples === 0) {
    return (
      <div className="text-sm text-center py-8" style={{ color: "var(--text-secondary)" }}>
        No samples for this tag in the last {data.weeks} week{data.weeks === 1 ? "" : "s"}.
      </div>
    );
  }

  const gMin = data.global_min ?? 0;
  const gMax = data.global_max ?? 0;
  const span = Math.max(1e-9, gMax - gMin);

  // SVG dimensions
  const gridW = COL_WIDTH * 24;
  const gridH = ROW_HEIGHT * 7;
  const svgW = LABEL_GUTTER + gridW + 8;
  const svgH = TOP_GUTTER + gridH + 8;

  const emptyColor = cssVar("--ios-gray-5", "#E5E5EA");

  return (
    <div className="relative">
      {/* Heading line */}
      <div className="flex items-baseline gap-2 mb-2 flex-wrap">
        <div className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
          {data.tag_name}
        </div>
        {data.engineering_unit && (
          <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
            ({data.engineering_unit})
          </span>
        )}
        <div className="text-[11px] tabular-nums ml-auto" style={{ color: "var(--text-secondary)" }}>
          {data.total_samples.toLocaleString()} samples · last {data.weeks}w · range {formatValue(gMin)}–{formatValue(gMax)}
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "center" }}>
        <svg
          width={svgW}
          height={svgH}
          style={{ display: "block", maxWidth: "100%" }}
        >
          {/* Hour-of-day column labels (every 2 hours, top gutter) */}
          {Array.from({ length: 24 }, (_, h) => h).filter((h) => h % 2 === 0).map((h) => (
            <text
              key={`hh${h}`}
              x={LABEL_GUTTER + h * COL_WIDTH + COL_WIDTH / 2}
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

          {/* Day-of-week row labels (left gutter) */}
          {DOW_LABELS.map((label, idx) => (
            <text
              key={`dow${idx}`}
              x={LABEL_GUTTER - 8}
              y={TOP_GUTTER + idx * ROW_HEIGHT + ROW_HEIGHT / 2 + 3}
              textAnchor="end"
              style={{
                fontSize: 11,
                fill: "var(--text-secondary)",
              }}
            >
              {label}
            </text>
          ))}

          {/* The cells themselves */}
          {Array.from({ length: 7 }, (_, dowIdx) =>
            Array.from({ length: 24 }, (_, hour) => {
              const dow = dowIdx + 1;
              const cell = cellMap.get(`${dow}-${hour}`);
              const hasData = !!cell && cell.count > 0 && cell.avg != null;
              const t = hasData ? (cell!.avg! - gMin) / span : 0;
              const fill = hasData ? rampColor(t, ramp) : emptyColor;
              const x = LABEL_GUTTER + hour * COL_WIDTH + 1.5;
              const y = TOP_GUTTER + dowIdx * ROW_HEIGHT + 1.5;
              const w = COL_WIDTH - 3;
              const h = ROW_HEIGHT - 3;

              return (
                <rect
                  key={`c-${dow}-${hour}`}
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  rx={3}
                  ry={3}
                  fill={fill}
                  style={{ cursor: "crosshair" }}
                  onMouseEnter={(e) => {
                    const rect = (e.target as SVGRectElement).getBoundingClientRect();
                    setHover({
                      x: rect.left + rect.width / 2,
                      y: rect.top,
                      dow,
                      hour,
                      cell,
                    });
                  }}
                  onMouseLeave={() => setHover(null)}
                />
              );
            })
          )}
        </svg>
      </div>

      {/* Color-scale legend */}
      <div
        className="flex items-center justify-center gap-2 mt-2 text-[11px] tabular-nums"
        style={{ color: "var(--text-secondary)" }}
      >
        <span>{formatValue(gMin)}</span>
        <span
          style={{
            display: "inline-block",
            width: 140,
            height: 10,
            background: `linear-gradient(to right, ${ramp.map((s) => s.color).join(", ")})`,
            borderRadius: 3,
            border: "0.5px solid var(--separator)",
          }}
        />
        <span>{formatValue(gMax)} {data.engineering_unit ?? ""}</span>
      </div>

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
            maxWidth: 240,
          }}
        >
          <div className="font-medium">
            {DOW_LABELS[hover.dow - 1]} · {hover.hour.toString().padStart(2, "0")}:00 – {((hover.hour + 1) % 24).toString().padStart(2, "0")}:00
          </div>
          {hover.cell && hover.cell.count > 0 ? (
            <>
              <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 tabular-nums">
                <span style={{ color: "var(--text-secondary)" }}>Avg</span>
                <span className="font-medium">{formatValue(hover.cell.avg)} {data.engineering_unit ?? ""}</span>
                <span style={{ color: "var(--text-secondary)" }}>Min</span>
                <span>{formatValue(hover.cell.min)}</span>
                <span style={{ color: "var(--text-secondary)" }}>Max</span>
                <span>{formatValue(hover.cell.max)}</span>
                <span style={{ color: "var(--text-secondary)" }}>Samples</span>
                <span>{hover.cell.count.toLocaleString()}</span>
              </div>
            </>
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
// Card wrapper — own tag picker + weeks picker. Use this when dropping the
// heatmap onto a page that doesn't already have a single tag selected.

interface TagOption {
  id: number;
  name: string;
  engineering_unit: string | null;
}

export interface CalendarHeatmapCardProps {
  /** Tag selected elsewhere (e.g. the Trend chart). Optional. */
  initialTagId?: number;
}

export function CalendarHeatmapCard({ initialTagId }: CalendarHeatmapCardProps) {
  const [tagId, setTagId] = useState<number | null>(initialTagId ?? null);
  const [weeks, setWeeks] = useState<number>(4);

  // Sync to changes in initialTagId — if the parent (Trend page) changes
  // the selected tag, we follow along until the user explicitly chooses
  // a different one inside this card.
  const [overrode, setOverrode] = useState(false);
  useEffect(() => {
    if (!overrode && initialTagId != null) setTagId(initialTagId);
  }, [initialTagId, overrode]);

  // Lightweight tag dropdown — uses /trends/tags but only the id+name fields.
  const tagsQuery = useQuery({
    queryKey: ["trend-tags-min"],
    queryFn: () => api.get<TagOption[]>("/trends/tags?limit=2000"),
    staleTime: 5 * 60_000,
  });

  return (
    <SectionCard
      title="Calendar heatmap"
      subtitle="Average value by day-of-week × hour-of-day — reveals time-based patterns"
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
                {t.name}{t.engineering_unit ? ` (${t.engineering_unit})` : ""}
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
                style={weeks === w
                  ? { backgroundColor: "var(--bg-elevated)", color: "var(--text-primary)" }
                  : { color: "var(--text-secondary)" }}
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
          <div>Pick a tag from the dropdown above to see its weekly pattern.</div>
        </div>
      ) : (
        <CalendarHeatmap tagId={tagId} weeks={weeks} />
      )}
    </SectionCard>
  );
}
