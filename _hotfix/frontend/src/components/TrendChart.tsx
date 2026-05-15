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
  forwardRef, useEffect, useImperativeHandle, useRef, useState,
} from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type {
  TrendHistoryResponse, TrendPoint, TrendSeries,
} from "@/types/api";
import { AlertTriangle } from "lucide-react";

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
};

const TrendChart = forwardRef<TrendChartHandle, TrendChartProps>(
  function TrendChart({ history, height = 420 }, ref) {
    const containerRef = useRef<HTMLDivElement>(null);
    const plotRef = useRef<uPlot | null>(null);
    const [error, setError] = useState<string | null>(null);

    // History is captured into a ref so the imperative exportPNG handler
    // always has access to the *current* data (refs don't trigger renders
    // but update synchronously when the prop changes).
    const historyRef = useRef(history);
    useEffect(() => { historyRef.current = history; }, [history]);

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
          const { data, opts } = buildChart(history, width, height);
          console.log("[TrendChart] mounting uPlot", {
            width, height,
            seriesCount: opts.series?.length ?? 0,
            scaleCount: Object.keys(opts.scales ?? {}).length,
            axisCount: opts.axes?.length ?? 0,
            xLen: data[0]?.length ?? 0,
          });
          plotRef.current = new uPlot(opts, data, container);
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
    }, [history, height]);

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
        <div ref={containerRef} className="w-full" style={{ height }} />
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
    {},  // X axis placeholder
    ...history.series.map((s, idx) => {
      const color = TAG_COLORS[idx % TAG_COLORS.length];
      const isStepType = STEP_DATA_TYPES.has(s.data_type);
      const useStep = isStepType && stepBuilder != null;

      return {
        label: s.engineering_unit
          ? `${s.tag_name} (${s.engineering_unit})`
          : s.tag_name,
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
  const { scales, axes } = buildScalesAndAxes(history.series, scaleAssignment);

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
): { scales: uPlot.Options["scales"]; axes: uPlot.Options["axes"] } {
  const scales: NonNullable<uPlot.Options["scales"]> = {
    x: { time: true },
  };
  const axes: NonNullable<uPlot.Options["axes"]> = [
    {
      stroke: "#5b6573",
      grid: { stroke: "#eef0f3", width: 1 },
      ticks: { show: true, stroke: "#5b6573" },
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
