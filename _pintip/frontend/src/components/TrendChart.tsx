/**
 * Phase 13.3d — Trend chart with quality markers + PNG export.
 *
 * Everything that worked in 13.3b stays — multi-axis, limit lines, step
 * trends — guarded the same way. Two new features layered on top:
 *
 *   - Quality markers: for each tag whose data contains bad (st<64) or
 *     uncertain (64≤st<128) points, an extra "marker" series is added
 *     with sparse data (only at the problem timestamps) and zero stroke
 *     (no line — just colored dots). In raw aggregation these come
 *     from the per-point `st` field; in 1m/1h/1d buckets they come
 *     from the bucket's bad-count `b`. Skipped entirely for clean
 *     series so the legend stays focused.
 *
 *   - exportPNG: imperative handle on the component lets the parent
 *     trigger a chart screenshot. Uses uPlot's canvas directly (axes,
 *     grid, and series — legend is HTML so it's not captured, which
 *     matches typical operator usage: paste into a ticket and describe
 *     the tags in your own words).
 *
 * If anything in either new feature throws, the base chart still
 * renders — guards are individual.
 */
import {
  forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type {
  TrendHistoryResponse, TrendPoint, TrendSeries, TrendTag,
} from "@/types/api";
import { api } from "@/lib/api";
import { useTimeFormat } from "@/lib/timeFormat";
import { AlertTriangle } from "lucide-react";
import TrendTooltip, {
  type CursorState, type CursorSeriesData,
} from "@/components/TrendTooltip";

const TAG_COLORS = [
  "#14a06e", "#2563eb", "#b45309", "#7c3aed", "#dc2626", "#0d5e6e",
];

// Right-angle / "step after" rendering is appropriate for discrete-state
// values: booleans, valve positions, fault flags, enum codes.
const STEP_DATA_TYPES = new Set(["bool", "uint8", "int8", "uint16", "int16"]);

// Quality marker colors — chosen to read clearly against light + dark
// backgrounds without competing with the tag's own line color.
const QUALITY_BAD_COLOR = "#dc2626";        // red-600
const QUALITY_UNCERTAIN_COLOR = "#d97706";  // amber-600

export type TrendChartHandle = {
  exportPNG(): void;
};

type TrendChartProps = {
  history: TrendHistoryResponse;
  height?: number;
  /** "full" (default), "compact", or "off". Operator preference from
   *  the TooltipModeSelector. When "off", the cursor hook short-circuits
   *  and no overlay is rendered. */
  tooltipMode?: "full" | "compact" | "off";
};

const TrendChart = forwardRef<TrendChartHandle, TrendChartProps>(
  function TrendChart({ history, height = 420, tooltipMode = "full" }, ref) {
    const containerRef = useRef<HTMLDivElement>(null);
    const plotRef = useRef<uPlot | null>(null);
    const [error, setError] = useState<string | null>(null);

    // Time format preference from the global context. Re-mounts the chart
    // (via the useEffect dep below) when the operator flips the toggle, so
    // axis labels switch immediately.
    const { is24h } = useTimeFormat();

    // Cursor state - drives the rich tooltip overlay. Updated by uPlot's
    // setCursor hook (registered below).
    const [cursorState, setCursorState] = useState<CursorState | null>(null);

    // Pinned cursor - set when the operator clicks the chart, allowing
    // them to interact with the tooltip (scroll, hover) without it
    // following the mouse and flickering. Click again to unpin.
    const [pinnedCursor, setPinnedCursor] = useState<CursorState | null>(null);

    // Ref mirror of cursorState so the click handler (attached once at
    // mount) sees the latest hover position without re-binding.
    const cursorStateRef = useRef<CursorState | null>(null);
    useEffect(() => { cursorStateRef.current = cursorState; }, [cursorState]);

    // When operator clicks the chart container without dragging, toggle
    // pin: pin at current hover position, or unpin if already pinned.
    // A small drag threshold prevents accidental pins during zoom-select.
    useEffect(() => {
      const container = containerRef.current;
      if (!container) return;
      let downAt: { x: number; y: number } | null = null;
      const onDown = (e: MouseEvent) => {
        downAt = { x: e.clientX, y: e.clientY };
      };
      const onUp = (e: MouseEvent) => {
        if (!downAt) return;
        const dx = Math.abs(e.clientX - downAt.x);
        const dy = Math.abs(e.clientY - downAt.y);
        downAt = null;
        if (dx >= 5 || dy >= 5) return; // it was a drag (zoom select)
        setPinnedCursor((prev) => prev ? null : cursorStateRef.current);
      };
      container.addEventListener("mousedown", onDown);
      container.addEventListener("mouseup", onUp);
      return () => {
        container.removeEventListener("mousedown", onDown);
        container.removeEventListener("mouseup", onUp);
      };
    }, []);

    // Tooltip mode read via a ref so the setCursor hook (which closes over
    // the build-time value) always sees the latest preference without
    // forcing a chart re-mount on every flip.
    const tooltipModeRef = useRef(tooltipMode);
    useEffect(() => {
      tooltipModeRef.current = tooltipMode;
      // If switched to "off", clear any visible tooltip immediately.
      if (tooltipMode === "off") setCursorState(null);
    }, [tooltipMode]);

    // One-time CSS injection: hide quality-marker rows from uPlot's
    // built-in legend. We can't suppress them at the series level (uPlot
    // doesn't support per-series legend visibility), but each marker
    // series carries `class: "u-marker-row"` and uPlot adds that class
    // to the corresponding <tr>, so a single CSS rule does the job.
    useEffect(() => {
      // ID is bumped (v2) because an earlier version of this stylesheet
      // only hid marker rows. The presence guard would otherwise skip
      // injecting the expanded ruleset on upgrade.
      if (document.getElementById("trend-marker-legend-style-v2")) return;
      // Remove the prior version if it's lingering from a stale tab.
      document.getElementById("trend-marker-legend-style")?.remove();
      const style = document.createElement("style");
      style.id = "trend-marker-legend-style-v2";
      style.textContent = `
        /* Hide quality-marker ghost series rows from the legend. */
        .u-legend .u-marker-row { display: none !important; }

        /* Constrain inline-legend rows so long tag names don't spill into
           neighboring series cells. Without these rules uPlot's default
           layout gives each label unbounded width on one line. */
        .u-legend.u-inline {
          text-align: left !important;
          padding: 4px 6px !important;
        }
        .u-legend.u-inline tr {
          margin-right: 18px !important;
          margin-bottom: 2px !important;
          vertical-align: top;
        }
        .u-legend .u-label {
          display: inline-block;
          max-width: 200px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          vertical-align: middle;
        }
        .u-legend .u-value {
          font-variant-numeric: tabular-nums;
        }
      `;
      document.head.appendChild(style);
    }, []);

    // Tag metadata for the tooltip's device/protocol/block/address fields.
    // Reuses the SAME queryKey as TagPicker - React Query dedupes by key,
    // so this is essentially free at runtime.
    const tagsQuery = useQuery({
      queryKey: ["trend-tags"],
      queryFn: () => api.get<TrendTag[]>("/trends/tags?enabled_only=false&limit=2000"),
      staleTime: 30_000,
    });
    const tagsMetaMap = useMemo<Record<number, TrendTag>>(() => {
      const map: Record<number, TrendTag> = {};
      (tagsQuery.data ?? []).forEach((t) => { map[t.id] = t; });
      return map;
    }, [tagsQuery.data]);

    // History is captured into a ref so the imperative exportPNG handler
    // always has access to the *current* data (refs don't trigger renders
    // but update synchronously when the prop changes).
    const historyRef = useRef(history);
    useEffect(() => { historyRef.current = history; }, [history]);

    // Legend visibility persistence. uPlot is destroyed and recreated
    // every time `history` changes — including every 5s in live mode —
    // which means any series the operator hid by clicking its legend
    // entry pops back as soon as new data arrives. We persist hidden
    // series labels in a ref (survives re-mounts; doesn't trigger
    // re-renders), and after each mount we restore visibility to match.
    const hiddenLabelsRef = useRef<Set<string>>(new Set());

    // Imperative API for the parent component.
    useImperativeHandle(ref, () => ({
      exportPNG() {
        const u = plotRef.current;
        const h = historyRef.current;
        if (!u || !h) return;

        try {
          const source = u.ctx.canvas;
          const sourceW = source.width;
          const sourceH = source.height;
          const dpr = window.devicePixelRatio || 1;

          // Dimensions in canvas pixels (already DPR-scaled).
          const padding = 16 * dpr;
          const titleH = 28 * dpr;
          const subtitleH = 18 * dpr;
          const headerH = padding + titleH + subtitleH + 12 * dpr;

          const legendRowH = 22 * dpr;
          const legendH = padding + h.series.length * legendRowH + 6 * dpr;

          const footerH = 22 * dpr;

          const outW = sourceW + padding * 2;
          const outH = sourceH + headerH + legendH + footerH;

          const out = document.createElement("canvas");
          out.width = outW;
          out.height = outH;
          const ctx = out.getContext("2d");
          if (!ctx) {
            console.error("[TrendChart] 2D context unavailable for PNG export");
            return;
          }

          // White background — fixes the "black PNG" problem with viewers
          // that show transparent canvas as black.
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, outW, outH);

          // Title — comma-separated tag names with units
          ctx.fillStyle = "#0f172a";
          ctx.font = `bold ${15 * dpr}px sans-serif`;
          ctx.textAlign = "left";
          ctx.textBaseline = "top";
          const titleText = h.series
            .map((s) =>
              s.engineering_unit
                ? `${s.tag_name} (${s.engineering_unit})`
                : s.tag_name,
            )
            .join(", ");
          ctx.fillText(
            `InduVista Trend — ${truncate(titleText, 90)}`,
            padding, padding,
          );

          // Subtitle — time range + aggregation
          ctx.fillStyle = "#475569";
          ctx.font = `${11 * dpr}px sans-serif`;
          const startLocal = new Date(h.start).toLocaleString();
          const endLocal = new Date(h.end).toLocaleString();
          ctx.fillText(
            `${startLocal} → ${endLocal}  ·  ${h.aggregation} aggregation  ·  ${h.series.reduce((a, s) => a + s.returned_count, 0).toLocaleString()} points`,
            padding, padding + titleH,
          );

          // Chart canvas — drawn underneath header
          ctx.drawImage(source, padding, headerH);

          // Legend block — colored square + tag name + unit, one per row
          const legendStartY = headerH + sourceH + padding;
          h.series.forEach((s, i) => {
            const color = TAG_COLORS[i % TAG_COLORS.length];
            const y = legendStartY + i * legendRowH;

            ctx.fillStyle = color;
            ctx.fillRect(padding, y + 3 * dpr, 14 * dpr, 14 * dpr);

            ctx.fillStyle = "#0f172a";
            ctx.font = `${12 * dpr}px sans-serif`;
            ctx.textBaseline = "top";
            const label = s.engineering_unit
              ? `${s.tag_name} (${s.engineering_unit})`
              : s.tag_name;
            const extra = `${s.returned_count.toLocaleString()} pts`;
            ctx.fillText(`${label}  —  ${extra}`, padding + 22 * dpr, y + 4 * dpr);
          });

          // Footer — export timestamp, right-aligned
          ctx.font = `${10 * dpr}px sans-serif`;
          ctx.fillStyle = "#94a3b8";
          ctx.textAlign = "right";
          ctx.fillText(
            `Exported ${new Date().toLocaleString()}`,
            outW - padding, outH - footerH + 4 * dpr,
          );

          // Trigger download
          out.toBlob((blob) => {
            if (!blob) {
              console.error("[TrendChart] PNG blob generation failed");
              return;
            }
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `trend_${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
          }, "image/png");
        } catch (e) {
          console.error("[TrendChart] PNG export failed:", e);
        }
      },
    }), []);

    useEffect(() => {
      const container = containerRef.current;
      if (!container) return;

      plotRef.current?.destroy();
      plotRef.current = null;
      setError(null);

      if (!history.series.length) return;

      const tryMount = () => {
        if (!container) return;
        const width = container.clientWidth;
        if (width < 50) return;

        try {
          const { data, opts } = buildChart(history, width, height, is24h);

          // Legend visibility persistence — inject a setSeries hook that
          // mirrors uPlot's internal visibility state into our ref. We
          // append (not replace) so we don't clobber any hooks already
          // wired up by buildChart in the future.
          opts.hooks = opts.hooks || {};
          opts.hooks.setSeries = [
            ...(opts.hooks.setSeries ?? []),
            (u: uPlot, sidx: number | null, sopts: { show?: boolean }) => {
              if (sidx === null || sidx === 0) return;
              const s = u.series[sidx];
              if (!s?.label) return;
              if (sopts.show === false) hiddenLabelsRef.current.add(s.label);
              else if (sopts.show === true) hiddenLabelsRef.current.delete(s.label);
            },
          ];

          // Rich tooltip — uPlot fires setCursor on every cursor move that
          // changes the rounded data index. We snapshot the values at the
          // cursor, look up the aligned ST/bucket-quality, and hand the
          // payload to React state for the overlay to render.
          //
          // The aligned ST/good/bad arrays are computed in buildChart and
          // exposed on opts via the closure below (we re-derive here from
          // history + data to keep the hook self-contained).
          const xs = data[0] as readonly number[];
          const stByIdx: (number | null)[][] = history.series.map((s) => {
            const m = new Map<number, number>();
            s.points.forEach((p) => {
              const ts = Math.floor(new Date(p.t).getTime() / 1000);
              if (p.st != null) m.set(ts, p.st);
            });
            return xs.map((ts) => m.get(ts) ?? null);
          });
          const goodByIdx: (number | null)[][] = history.series.map((s) => {
            const m = new Map<number, number>();
            s.points.forEach((p) => {
              const ts = Math.floor(new Date(p.t).getTime() / 1000);
              if (p.g != null) m.set(ts, p.g);
            });
            return xs.map((ts) => m.get(ts) ?? null);
          });
          const badByIdx: (number | null)[][] = history.series.map((s) => {
            const m = new Map<number, number>();
            s.points.forEach((p) => {
              const ts = Math.floor(new Date(p.t).getTime() / 1000);
              if (p.b != null) m.set(ts, p.b);
            });
            return xs.map((ts) => m.get(ts) ?? null);
          });

          opts.hooks.setCursor = [
            ...(opts.hooks.setCursor ?? []),
            (u: uPlot) => {
              // Honor "off" mode without re-mounting the chart.
              if (tooltipModeRef.current === "off") {
                if (cursorState !== null) setCursorState(null);
                return;
              }
              const idx = u.cursor.idx;
              if (idx == null || idx < 0 || idx >= xs.length) {
                setCursorState(null);
                return;
              }
              const ts = xs[idx];
              const seriesData: CursorSeriesData[] = history.series.map((s, i) => ({
                tagId: s.tag_id,
                tagName: s.tag_name,
                color: TAG_COLORS[i % TAG_COLORS.length],
                engineeringUnit: s.engineering_unit,
                value: (u.data[i + 1] as (number | null)[] | undefined)?.[idx] ?? null,
                st: stByIdx[i]?.[idx] ?? null,
                goodCount: goodByIdx[i]?.[idx] ?? null,
                badCount: badByIdx[i]?.[idx] ?? null,
              }));
              setCursorState({
                timestampSec: ts,
                mouseLeft: u.cursor.left ?? 0,
                mouseTop: u.cursor.top ?? 0,
                series: seriesData,
              });
            },
          ];

          console.log("[TrendChart] mounting uPlot", {
            width, height,
            seriesCount: opts.series?.length ?? 0,
            scaleCount: Object.keys(opts.scales ?? {}).length,
            axisCount: opts.axes?.length ?? 0,
            xLen: data[0]?.length ?? 0,
            restoringHidden: hiddenLabelsRef.current.size,
          });
          plotRef.current = new uPlot(opts, data, container);

          // After mount, restore the hidden state from our ref so the
          // operator's legend toggles survive data refreshes (which
          // happen on every poll tick in live mode).
          plotRef.current.series.forEach((s, idx) => {
            if (idx === 0 || !s.label) return;
            if (hiddenLabelsRef.current.has(s.label)) {
              try {
                plotRef.current!.setSeries(idx, { show: false });
              } catch (e) {
                console.warn("[TrendChart] could not restore hidden state for", s.label, e);
              }
            }
          });
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : String(e);
          console.error("[TrendChart] uPlot mount failed:", e);
          setError(msg);
        }
      };

      tryMount();

      const resizeObs = new ResizeObserver(() => {
        if (!container) return;
        const width = container.clientWidth;
        if (width < 50) return;
        if (plotRef.current) {
          try { plotRef.current.setSize({ width, height }); }
          catch (e) { console.error("[TrendChart] resize failed:", e); }
        } else {
          tryMount();
        }
      });
      resizeObs.observe(container);

      return () => {
        resizeObs.disconnect();
        plotRef.current?.destroy();
        plotRef.current = null;
      };
    }, [history, height, is24h]);

    if (error) {
      return (
        <div
          className="border border-destructive/30 bg-destructive/5 rounded-md p-4 text-sm flex items-start gap-2"
          style={{ minHeight: height }}
        >
          <AlertTriangle className="h-4 w-4 text-destructive flex-shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-medium text-destructive">Chart failed to render</p>
            <p className="text-xs text-muted-foreground font-mono">{error}</p>
          </div>
        </div>
      );
    }

    return (
      <div className="w-full" style={{ minHeight: height }}>
        <div ref={containerRef} className="w-full relative" style={{ height }}>
          <TrendTooltip
            cursor={pinnedCursor ?? cursorState}
            tagsMeta={tagsMetaMap}
            isAggregated={history.aggregation !== "raw"}
            mode={tooltipMode}
            isPinned={pinnedCursor != null}
            onUnpin={() => setPinnedCursor(null)}
            containerWidth={containerRef.current?.clientWidth ?? 0}
            containerHeight={height}
          />
        </div>
      </div>
    );
  },
);

export default TrendChart;

// ---------------------------------------------------------------------------
// uPlot config builder
// ---------------------------------------------------------------------------

function buildChart(
  history: TrendHistoryResponse,
  width: number,
  height: number,
  is24h: boolean,
): { data: uPlot.AlignedData; opts: uPlot.Options } {
  // ---- 1. Build the X axis ----------------------------------------------
  const allXs = new Set<number>();
  history.series.forEach((s) =>
    s.points.forEach((p) => {
      const t = new Date(p.t).getTime();
      if (!isNaN(t)) allXs.add(Math.floor(t / 1000));
    }),
  );
  const xs = Array.from(allXs).sort((a, b) => a - b);

  // ---- 2. Build per-series Y arrays -------------------------------------
  const ys: (number | null)[][] = history.series.map((s) => {
    const lookup = new Map<number, number | null>();
    s.points.forEach((p) => {
      const t = new Date(p.t).getTime();
      if (!isNaN(t)) lookup.set(Math.floor(t / 1000), p.v ?? null);
    });
    return xs.map((t) => lookup.get(t) ?? null);
  });

  // ---- 3. Scale assignment ---------------------------------------------
  const scaleAssignment = planScales(history.series);
  const stepBuilder = safeStepBuilder();

  // ---- 4. Build the main series configs --------------------------------
  const series: uPlot.Series[] = [
    {
      // X-axis series. The `value` callback drives the cursor-position
      // display in uPlot's live legend at the bottom of the chart.
      // Without an override, uPlot uses its built-in 24h formatter and
      // ignores the operator's 12h/24h preference.
      value: (_u: uPlot, v: number | null) => {
        if (v == null) return "";
        return formatLegendTimestamp(v, is24h);
      },
    },
    ...history.series.map((s, idx) => {
      const color = TAG_COLORS[idx % TAG_COLORS.length];
      const isStepType = STEP_DATA_TYPES.has(s.data_type);
      const useStep = isStepType && stepBuilder != null;

      return {
        // Truncate at 22 chars so the legend's inline row stays compact.
        // The full tag name remains visible in the tooltip, summary panel,
        // and Live Value Panel - the legend is just a colour-to-name key.
        label: s.engineering_unit
          ? `${truncate(s.tag_name, 22)} (${s.engineering_unit})`
          : truncate(s.tag_name, 28),
        stroke: color,
        width: 1.6,
        spanGaps: false,
        scale: scaleAssignment[idx],
        ...(useStep ? { paths: stepBuilder! } : {}),
        value: (_u: uPlot, v: number | null) =>
          v == null ? "—" : formatValue(v),
      } satisfies uPlot.Series;
    }),
  ];

  // ---- 5. Quality marker series (Phase 13.3d, restored aggregated) -----
  // In raw mode: per-point st-based markers (7px = one bad reading).
  // In aggregated mode: bucket-level markers (4px = bucket had ≥1 bad
  // reading). Different sizes signal different semantics so operators
  // can tell at a glance whether they're looking at point-level or
  // bucket-level quality data.
  const markerSeries: { seriesConfig: uPlot.Series; data: (number | null)[] }[] = [];
  const isRaw = history.aggregation === "raw";
  const badMarkerSize = isRaw ? 7 : 4;
  const uncertainMarkerSize = isRaw ? 6 : 4;

  history.series.forEach((s, idx) => {
    const scale = scaleAssignment[idx];
    try {
      const badData = buildQualityMarkerData(s.points, xs, "bad", isRaw);
      const badCount = badData.filter((v) => v != null).length;
      const uncertainData = buildQualityMarkerData(s.points, xs, "uncertain", isRaw);
      const uncertainCount = uncertainData.filter((v) => v != null).length;

      console.log(`[TrendChart] quality markers for ${s.tag_name}:`, {
        aggregation: history.aggregation,
        totalPoints: s.points.length,
        badCount,
        uncertainCount,
        note: isRaw
          ? "raw mode: 1 marker = 1 bad reading"
          : "aggregated mode: 1 marker = bucket containing ≥1 bad reading",
      });

      if (badCount > 0) {
        markerSeries.push({
          seriesConfig: {
            label: `${s.tag_name} · bad`,
            stroke: "transparent",
            scale,
            spanGaps: false,
            class: "u-marker-row",
            points: {
              show: true,
              size: badMarkerSize,
              fill: QUALITY_BAD_COLOR,
              stroke: QUALITY_BAD_COLOR,
            },
            value: (_u, v: number | null) =>
              v == null ? "—" : formatValue(v),
          },
          data: badData,
        });
      }

      if (uncertainCount > 0) {
        markerSeries.push({
          seriesConfig: {
            label: `${s.tag_name} · uncertain`,
            stroke: "transparent",
            scale,
            spanGaps: false,
            class: "u-marker-row",
            points: {
              show: true,
              size: uncertainMarkerSize,
              fill: QUALITY_UNCERTAIN_COLOR,
              stroke: QUALITY_UNCERTAIN_COLOR,
            },
            value: (_u, v: number | null) =>
              v == null ? "—" : formatValue(v),
          },
          data: uncertainData,
        });
      }
    } catch (e) {
      console.warn(`[TrendChart] quality markers failed for ${s.tag_name}:`, e);
    }
  });

  // Append marker series + their data
  markerSeries.forEach((m) => series.push(m.seriesConfig));
  const allYs = [...ys, ...markerSeries.map((m) => m.data)];

  // ---- 6. Scales + axes -------------------------------------------------
  const { scales, axes } = buildScalesAndAxes(history.series, scaleAssignment, is24h);

  // ---- 7. Plot options --------------------------------------------------
  const opts: uPlot.Options = {
    width,
    height,
    series,
    scales,
    axes,
    cursor: { drag: { x: true, y: false } },
    legend: { show: true, live: true },
    hooks: {
      draw: [
        // Limit lines: only when ≤ 2 tags selected — chart stays readable.
        (u) => {
          if (history.series.length > 2) return;
          try {
            history.series.forEach((s, idx) => {
              const scaleKey = scaleAssignment[idx];
              const color = TAG_COLORS[idx % TAG_COLORS.length];
              drawLimitLine(u, scaleKey, s.min_value, color, `min ${s.tag_name}`);
              drawLimitLine(u, scaleKey, s.max_value, color, `max ${s.tag_name}`);
            });
          } catch (e) {
            console.warn("[TrendChart] limit line draw failed:", e);
          }
        },
      ],
    },
  };

  return { data: [xs, ...allYs] as uPlot.AlignedData, opts };
}

// ---------------------------------------------------------------------------
// Quality marker helpers
// ---------------------------------------------------------------------------

/**
 * Returns an array of values aligned to `xs`, with a value present only
 * where the original point's quality matches `target`.
 *
 * Raw mode (`isRaw=true`): uses per-point `st` field. A reading is bad
 * if st<64, uncertain if 64≤st<128. One marker = one reading.
 *
 * Aggregated mode (`isRaw=false`): uses bucket `b` (bad-count) field. A
 * bucket is "bad" if it contains ≥1 reading with st<64. One marker =
 * one bucket containing bad data. Uncertain markers are not rendered in
 * aggregated mode because the backend's bucket payload doesn't track
 * uncertain-count separately (only good `g` and bad `b`).
 */
function buildQualityMarkerData(
  points: TrendPoint[],
  xs: number[],
  target: "bad" | "uncertain",
  isRaw: boolean,
): (number | null)[] {
  const lookup = new Map<number, number>();

  for (const p of points) {
    if (p.v == null) continue;
    const t = new Date(p.t).getTime();
    if (isNaN(t)) continue;

    let matches = false;
    if (isRaw) {
      if (p.st === undefined) continue;
      if (target === "bad" && p.st < 64) matches = true;
      else if (target === "uncertain" && p.st >= 64 && p.st < 128) matches = true;
    } else {
      // Aggregated mode — only "bad" markers, derived from bucket b-count.
      if (target === "bad" && p.b != null && p.b > 0) matches = true;
    }

    if (matches) {
      lookup.set(Math.floor(t / 1000), p.v);
    }
  }

  return xs.map((t) => lookup.get(t) ?? null);
}

// ---------------------------------------------------------------------------
// Scale planning + axes
// ---------------------------------------------------------------------------

function planScales(series: TrendSeries[]): string[] {
  const uniqueUnits: string[] = [];
  for (const s of series) {
    const u = s.engineering_unit ?? "_unitless";
    if (!uniqueUnits.includes(u)) uniqueUnits.push(u);
  }
  if (uniqueUnits.length > 3 || uniqueUnits.length === 1) {
    return series.map(() => "y");
  }
  const keyFor = new Map(uniqueUnits.map((u, i) => [u, i === 0 ? "y" : `y${i + 1}`]));
  return series.map((s) => keyFor.get(s.engineering_unit ?? "_unitless")!);
}

function buildScalesAndAxes(
  series: TrendSeries[],
  scaleAssignment: string[],
  is24h: boolean,
): { scales: uPlot.Options["scales"]; axes: uPlot.Options["axes"] } {
  const scales: NonNullable<uPlot.Options["scales"]> = {
    x: { time: true },
  };
  const axes: NonNullable<uPlot.Options["axes"]> = [
    {
      stroke: "#5b6573",
      grid: { stroke: "#eef0f3", width: 1 },
      ticks: { show: true, stroke: "#5b6573" },
      // Spec 8.5 + user pref - per-tick time format. uPlot's default
      // formatter is 24h-only; we override to support 12h with AM/PM.
      // The tick increment (foundIncr, in seconds) decides whether to
      // show a date prefix (>= 1 day) and seconds (< 1 minute).
      values: (_self, splits, _axisIdx, _foundSpace, foundIncr) =>
        splits.map((ts) => formatXAxisTick(ts, is24h, foundIncr)),
    },
  ];

  const seenScales = new Set<string>();
  scaleAssignment.forEach((key, idx) => {
    if (seenScales.has(key)) return;
    seenScales.add(key);

    scales[key] = { auto: true };

    const isFirst = seenScales.size === 1;
    const unit = series[idx].engineering_unit ?? "";

    axes.push({
      scale: key,
      side: isFirst ? 3 : 1,
      stroke: "#5b6573",
      ...(isFirst
        ? { grid: { stroke: "#eef0f3", width: 1 } }
        : { grid: { show: false } }),
      size: 60,
      label: unit || undefined,
      labelSize: unit ? 16 : 0,
    });
  });

  return { scales, axes };
}

// ---------------------------------------------------------------------------
// Limit line drawing
// ---------------------------------------------------------------------------

function drawLimitLine(
  u: uPlot,
  scaleKey: string,
  value: number | null,
  color: string,
  label: string,
) {
  if (value == null) return;
  const y = u.valToPos(value, scaleKey, true);
  if (!isFinite(y)) return;
  if (y < u.bbox.top || y > u.bbox.top + u.bbox.height) return;

  const ctx = u.ctx;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.5;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(u.bbox.left, y);
  ctx.lineTo(u.bbox.left + u.bbox.width, y);
  ctx.stroke();

  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  ctx.fillStyle = color;
  ctx.font = "10px 'IBM Plex Mono', ui-monospace, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "bottom";
  ctx.fillText(label, u.bbox.left + u.bbox.width - 4, y - 2);
  ctx.restore();
}

// ---------------------------------------------------------------------------
// Step builder (safe accessor)
// ---------------------------------------------------------------------------

type PathBuilder = NonNullable<uPlot.Series["paths"]>;

function safeStepBuilder(): PathBuilder | null {
  try {
    const paths = (uPlot as unknown as {
      paths?: { stepped?: (opts: { align: 1 | -1 }) => PathBuilder };
    }).paths;
    if (!paths || typeof paths.stepped !== "function") {
      console.warn("[TrendChart] uPlot.paths.stepped unavailable — step trends will render as linear");
      return null;
    }
    return paths.stepped({ align: 1 });
  } catch (e) {
    console.warn("[TrendChart] step path builder construction failed:", e);
    return null;
  }
}

function formatValue(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 10000) return v.toFixed(0);
  if (abs >= 100) return v.toFixed(1);
  if (abs >= 1) return v.toFixed(2);
  return v.toFixed(3);
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

/**
 * Format a uPlot tick timestamp (Unix seconds) for the X axis, honoring
 * the user's 24h/12h preference and the tick increment.
 *
 * Rules:
 *   - Show seconds when ticks are sub-minute (high zoom)
 *   - Prepend MM/DD when ticks are >= 1 day apart (multi-day view)
 */
function formatXAxisTick(ts: number, is24h: boolean, incrSec: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  const showSeconds = incrSec < 60;
  const showDate = incrSec >= 86_400;

  let timeStr: string;
  if (is24h) {
    timeStr = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (showSeconds) timeStr += `:${pad(d.getSeconds())}`;
  } else {
    const h24 = d.getHours();
    const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
    const period = h24 >= 12 ? "PM" : "AM";
    timeStr = `${h12}:${pad(d.getMinutes())}`;
    if (showSeconds) timeStr += `:${pad(d.getSeconds())}`;
    timeStr += ` ${period}`;
  }

  if (showDate) {
    return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${timeStr}`;
  }
  return timeStr;
}

/**
 * Format a timestamp (Unix seconds) for uPlot's live legend at the bottom
 * of the chart. Always shows date + time with seconds since the live
 * legend is a single value display where the operator wants precision.
 */
function formatLegendTimestamp(ts: number, is24h: boolean): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  const datePart = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  let timeStr: string;
  if (is24h) {
    timeStr = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } else {
    const h24 = d.getHours();
    const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
    const period = h24 >= 12 ? "PM" : "AM";
    timeStr = `${h12}:${pad(d.getMinutes())}:${pad(d.getSeconds())} ${period}`;
  }
  return `${datePart} ${timeStr}`;
}

