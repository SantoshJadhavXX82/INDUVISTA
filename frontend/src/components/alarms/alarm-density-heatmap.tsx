/**
 * Phase 19 — AlarmDensityHeatmap
 *
 * Visualizes alarm activation density per rule over time. Unlike the
 * quality heatmap (4 discrete classes), this is a CONTINUOUS heat ramp:
 * the more activations in a bin, the hotter the cell.
 *
 * Why this matters:
 *   - "When during the day do we get the most alarms?" → vertical
 *     hot column at shift change times means batch transition noise
 *   - "Which rule is noisy?" → a horizontal hot band → that rule
 *     fires too often, retune the threshold
 *   - "Is anything fired off-hours?" → unexpected hot cells at 02:00
 *     suggest a runaway process
 *
 * The colour ramp is normalized to the response's max_count so the
 * gradient always uses its full dynamic range. Cells with zero
 * activations stay a near-invisible base tint.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";


type DensityRule = {
  rule_id: number;
  rule_name: string | null;
  tag_name: string;
  severity: string;
};

type DensityBin = { start: string };

type DensityResponse = {
  window_hours: number;
  bin_minutes: number;
  rules: DensityRule[];
  bins: DensityBin[];
  counts: number[][];
  max_count: number;
};

const LABEL_WIDTH = 260;
const SCROLLBAR_PAD = 14;
const TIME_AXIS_HEIGHT = 26;
const ROW_HEIGHT = 22;
const CELL_GAP = 1.5;
const CELL_RADIUS = 3;


function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}


/** Build a 5-stop ramp tuned for "alarm intensity": cool empty → warm
 *  amber → hot red → deep crimson. Each stop is also given a "glow"
 *  alpha that the canvas applies as shadowColor for the most-intense
 *  cells, making them feel hot. */
type Stop = { from: number; color: string; glowAlpha: number };

function readRamp(): Stop[] {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  if (isDark) {
    return [
      { from: 0.0,  color: "rgba(255,255,255,0.04)",  glowAlpha: 0 },     // empty
      { from: 0.05, color: "#3D2200",                  glowAlpha: 0 },     // 1 alarm — deep warm tint
      { from: 0.20, color: "#A85B00",                  glowAlpha: 0.10 },  // a few
      { from: 0.45, color: "#E27300",                  glowAlpha: 0.18 },  // warm
      { from: 0.75, color: "#FF5847",                  glowAlpha: 0.32 },  // hot
      { from: 1.00, color: "#FF1A1A",                  glowAlpha: 0.50 },  // peak
    ];
  }
  return [
    { from: 0.0,  color: "rgba(0,0,0,0.04)",  glowAlpha: 0 },
    { from: 0.05, color: "#FFEBC9",            glowAlpha: 0 },
    { from: 0.20, color: "#FFC061",            glowAlpha: 0.05 },
    { from: 0.45, color: "#FF8A3D",            glowAlpha: 0.18 },
    { from: 0.75, color: "#F04E2C",            glowAlpha: 0.32 },
    { from: 1.00, color: "#C5181C",            glowAlpha: 0.50 },
  ];
}


/** Linearly interpolate between two stops to pick a color for a 0-1 value. */
function rampColor(t: number, stops: Stop[]): { color: string; glow: number } {
  if (t <= 0) return { color: stops[0].color, glow: 0 };
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    if (t >= a.from && t <= b.from) {
      const f = (t - a.from) / (b.from - a.from || 1);
      // We interpolate the glowAlpha smoothly but use the upper stop's color
      // (cells snap to discrete heat tiers — easier to read at a glance).
      const color = t > (a.from + b.from) / 2 ? b.color : a.color;
      const glow = a.glowAlpha + f * (b.glowAlpha - a.glowAlpha);
      return { color, glow };
    }
  }
  const last = stops[stops.length - 1];
  return { color: last.color, glow: last.glowAlpha };
}


export interface AlarmDensityHeatmapProps {
  windowHours?: number;
  binMinutes?: number;
  severity?: string;
}


export function AlarmDensityHeatmap({
  windowHours = 24,
  binMinutes = 15,
  severity,
}: AlarmDensityHeatmapProps) {
  const density = useQuery({
    queryKey: ["alarms", "density-heatmap", windowHours, binMinutes, severity ?? "all"],
    queryFn: () => {
      const params = new URLSearchParams({
        window_hours: String(windowHours),
        bin_minutes: String(binMinutes),
      });
      if (severity) params.set("severity", severity);
      return api.get<DensityResponse>(`/alarms/density-heatmap?${params}`);
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 2,
    retryDelay: (n) => Math.min(1500 * 2 ** n, 8000),
  });

  const data = density.data;

  const [ramp, setRamp] = useState(() => readRamp());
  useEffect(() => {
    const obs = new MutationObserver(() => setRamp(readRamp()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState(800);
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 800;
      setContainerWidth(w);
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  // Canvas fills available horizontal space (everything to the right of
  // the label column). Cells are computed as floating-point widths and
  // snapped to integer pixels per cell so the row stretches edge-to-edge.
  const canvasWidth = useMemo(() => {
    if (!data) return 0;
    return Math.max(50, containerWidth - LABEL_WIDTH - SCROLLBAR_PAD);
  }, [containerWidth, data]);

  const cellWidth = useMemo(() => {
    if (!data || data.bins.length === 0) return 6;
    return Math.max(3, canvasWidth / data.bins.length);
  }, [canvasWidth, data]);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hoverIdx, setHoverIdx] = useState<{ row: number; col: number } | null>(null);

  useEffect(() => {
    if (!data || !canvasRef.current || data.rules.length === 0) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const cssWidth = canvasWidth;
    const cssHeight = data.rules.length * ROW_HEIGHT;
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${cssHeight}px`;
    canvas.width = cssWidth * dpr;
    canvas.height = cssHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);

    const cellH = ROW_HEIGHT - CELL_GAP;
    const maxCount = Math.max(1, data.max_count);

    // Helper to compute pixel-aligned cell rect for bin j
    const cellRect = (j: number) => {
      const x0 = Math.round(j * cellWidth);
      const x1 = Math.round((j + 1) * cellWidth);
      return { x0, w: Math.max(1, x1 - x0 - CELL_GAP) };
    };

    // Pass 1 — base color per cell
    for (let i = 0; i < data.rules.length; i++) {
      const row = data.counts[i];
      const y0 = i * ROW_HEIGHT;
      for (let j = 0; j < row.length; j++) {
        const count = row[j];
        const t = count === 0 ? 0 : Math.min(1, count / maxCount);
        const { color } = rampColor(t, ramp);
        const { x0, w } = cellRect(j);
        ctx.fillStyle = color;
        roundRect(ctx, x0, y0, w, cellH, CELL_RADIUS);
        ctx.fill();
      }
    }

    // Pass 2 — glow on the hottest cells
    for (let i = 0; i < data.rules.length; i++) {
      const row = data.counts[i];
      const y0 = i * ROW_HEIGHT;
      for (let j = 0; j < row.length; j++) {
        const count = row[j];
        if (count === 0) continue;
        const t = Math.min(1, count / maxCount);
        const { color, glow } = rampColor(t, ramp);
        if (glow < 0.1) continue;
        const { x0, w } = cellRect(j);
        ctx.save();
        ctx.shadowColor = color;
        ctx.shadowBlur = 4 + glow * 14;
        ctx.fillStyle = color;
        roundRect(ctx, x0, y0, w, cellH, CELL_RADIUS);
        ctx.fill();
        ctx.restore();
      }
    }

    // Pass 3 — hover cross-hair
    if (hoverIdx) {
      ctx.save();
      const overlay = document.documentElement.getAttribute("data-theme") === "dark"
        ? "rgba(255,255,255,0.06)"
        : "rgba(0,0,0,0.06)";
      ctx.fillStyle = overlay;
      ctx.fillRect(0, hoverIdx.row * ROW_HEIGHT, cssWidth, ROW_HEIGHT);
      const colX0 = Math.round(hoverIdx.col * cellWidth);
      const colX1 = Math.round((hoverIdx.col + 1) * cellWidth);
      ctx.fillRect(colX0, 0, colX1 - colX0, cssHeight);
      ctx.restore();
    }
  }, [data, ramp, cellWidth, hoverIdx]);

  const [hover, setHover] = useState<{
    x: number; y: number; ruleName: string; tagName: string; severity: string;
    binStart: string; count: number;
  } | null>(null);

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!data) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const bi = Math.floor(x / cellWidth);
    const ri = Math.floor(y / ROW_HEIGHT);
    if (ri < 0 || ri >= data.rules.length || bi < 0 || bi >= data.bins.length) {
      setHover(null);
      setHoverIdx(null);
      return;
    }
    setHoverIdx({ row: ri, col: bi });
    const r = data.rules[ri];
    setHover({
      x: e.clientX, y: e.clientY,
      ruleName: r.rule_name ?? `Rule #${r.rule_id}`,
      tagName: r.tag_name,
      severity: r.severity,
      binStart: data.bins[bi].start,
      count: data.counts[ri][bi],
    });
  };

  if (density.isLoading) {
    return (
      <div className="text-sm text-center py-8" style={{ color: "var(--text-secondary)" }}>
        Loading alarm density…
      </div>
    );
  }
  if (density.isError) {
    const message = density.error instanceof Error ? density.error.message : String(density.error);
    return (
      <div
        className="text-sm rounded-md px-3 py-3 flex items-start gap-3"
        style={{
          color: "var(--status-error-on-soft)",
          backgroundColor: "var(--status-error-soft)",
        }}
      >
        <div className="flex-1">
          <div className="font-medium">Couldn't load alarm density</div>
          <div className="text-[11px] mt-0.5 opacity-80">{message}</div>
        </div>
        <button
          type="button"
          onClick={() => density.refetch()}
          className="text-[11px] font-medium rounded px-2 py-1"
          style={{ backgroundColor: "var(--bg-elevated)", color: "var(--text-primary)" }}
        >
          Retry
        </button>
      </div>
    );
  }
  if (!data || data.rules.length === 0) {
    return (
      <div className="text-sm text-center py-8" style={{ color: "var(--text-secondary)" }}>
        No alarms have fired in this window. ✨
      </div>
    );
  }

  const labelTicks = makeTimeTicks(data.bins, cellWidth);
  const containerBg = document.documentElement.getAttribute("data-theme") === "dark"
    ? "radial-gradient(ellipse at top, rgba(255,255,255,0.025) 0%, rgba(255,255,255,0) 60%)"
    : "radial-gradient(ellipse at top, rgba(0,0,0,0.02) 0%, rgba(0,0,0,0) 60%)";

  return (
    <div ref={containerRef} className="relative">
      {/* Top info bar */}
      <div className="flex items-center gap-3 mb-3 flex-wrap">
        <div className="text-[11px] tabular-nums" style={{ color: "var(--text-secondary)" }}>
          {data.rules.length} rule{data.rules.length === 1 ? "" : "s"} fired ·
          {" "}{data.bins.length} bins × {data.bin_minutes}m · last {data.window_hours}h ·
          {" "}peak {data.max_count}/bin
        </div>

        {/* Continuous scale legend */}
        <div className="flex items-center gap-2 text-[11px] ml-auto" style={{ color: "var(--text-secondary)" }}>
          <span>0</span>
          <span
            style={{
              display: "inline-block",
              width: 90,
              height: 10,
              background: `linear-gradient(to right, ${ramp.map(s => s.color).join(", ")})`,
              borderRadius: 3,
              border: "0.5px solid var(--separator)",
            }}
          />
          <span className="tabular-nums">{data.max_count}+</span>
        </div>
      </div>

      <div
        className="flex"
        style={{
          maxHeight: 480,
          overflow: "auto",
          border: "0.5px solid var(--card-edge)",
          borderRadius: 12,
          background: containerBg,
          backgroundColor: "var(--bg-elevated-soft)",
        }}
      >
        {/* Label column */}
        <div
          style={{
            width: LABEL_WIDTH,
            flexShrink: 0,
            borderRight: "0.5px solid var(--card-edge)",
            position: "sticky",
            left: 0,
            zIndex: 1,
            backgroundColor: "var(--bg-elevated)",
          }}
        >
          {data.rules.map((r, i) => (
            <div
              key={r.rule_id}
              style={{
                height: ROW_HEIGHT,
                display: "flex",
                alignItems: "center",
                padding: "0 12px",
                gap: 6,
                borderBottom: "0.5px solid var(--separator)",
                fontSize: 11.5,
                color: "var(--text-primary)",
                backgroundColor: hoverIdx?.row === i ? "var(--ios-gray-5)" : "transparent",
                transition: "background-color 0.12s",
              }}
              onMouseEnter={() => setHoverIdx((h) => ({ row: i, col: h?.col ?? 0 }))}
              title={`${r.rule_name ?? r.tag_name} · ${r.severity}`}
            >
              <SeverityDot severity={r.severity} />
              <span style={{
                flex: 1,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                fontWeight: 500,
              }}>
                {r.rule_name ?? r.tag_name}
              </span>
              {r.rule_name && r.rule_name !== r.tag_name && (
                <span
                  style={{
                    fontSize: 10,
                    color: "var(--text-secondary)",
                    flexShrink: 0,
                  }}
                >
                  {r.tag_name}
                </span>
              )}
            </div>
          ))}
        </div>

        {/* Canvas */}
        <div style={{ position: "relative", padding: 2, flex: 1, minWidth: 0 }}>
          <canvas
            ref={canvasRef}
            onMouseMove={onMouseMove}
            onMouseLeave={() => { setHover(null); setHoverIdx(null); }}
            style={{ display: "block", cursor: "crosshair", borderRadius: 4 }}
          />
        </div>
      </div>

      {/* Time axis */}
      <div
        className="relative"
        style={{
          marginLeft: LABEL_WIDTH,
          marginTop: 6,
          height: TIME_AXIS_HEIGHT,
        }}
      >
        {labelTicks.map((tick, i) => (
          <div
            key={i}
            className="absolute text-[10px] tabular-nums"
            style={{
              left: tick.x,
              color: "var(--text-secondary)",
              transform: "translateX(-50%)",
            }}
          >
            <div style={{
              borderLeft: "0.5px solid var(--separator)",
              height: 5,
              marginLeft: "50%",
              marginBottom: 3,
            }} />
            {tick.label}
          </div>
        ))}
      </div>

      {hover && (
        <div
          className="fixed z-50 pointer-events-none rounded-lg px-3 py-2 text-[11px]"
          style={{
            left: hover.x + 14,
            top: hover.y + 14,
            backgroundColor: "var(--bg-elevated)",
            color: "var(--text-primary)",
            border: "0.5px solid var(--card-edge)",
            boxShadow: "0 4px 16px rgba(0,0,0,0.18)",
            maxWidth: 280,
          }}
        >
          <div className="flex items-center gap-2">
            <SeverityDot severity={hover.severity} />
            <span className="font-medium text-[12px]">{hover.ruleName}</span>
          </div>
          <div style={{ color: "var(--text-secondary)", marginTop: 2 }}>{hover.tagName}</div>
          <div className="mt-1.5 tabular-nums" style={{ color: "var(--text-secondary)" }}>
            {formatBinRange(hover.binStart, data.bin_minutes)}
          </div>
          <div className="mt-1.5 pt-1.5 flex items-center gap-2" style={{ borderTop: "0.5px solid var(--separator)" }}>
            <span style={{ fontSize: 18, fontWeight: 600, lineHeight: 1, color: "var(--text-primary)" }}>
              {hover.count}
            </span>
            <span style={{ color: "var(--text-secondary)" }}>
              activation{hover.count === 1 ? "" : "s"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}


function SeverityDot({ severity }: { severity: string }) {
  const color = severityColor(severity);
  return (
    <span
      style={{
        display: "inline-block",
        width: 7, height: 7,
        backgroundColor: color,
        borderRadius: 999,
        flexShrink: 0,
        boxShadow: `0 0 0 2px ${color}22`,
      }}
      aria-hidden="true"
    />
  );
}


function severityColor(sev: string): string {
  const s = sev.toLowerCase();
  if (s.includes("critical") && s.includes("v")) return cssVar("--severity-vcritical", "#9D0208");
  if (s.includes("critical")) return cssVar("--severity-critical", "#FF453A");
  if (s.includes("high")) return cssVar("--severity-high", "#FF9500");
  if (s.includes("medium") || s.includes("med")) return cssVar("--severity-medium", "#FFD60A");
  if (s.includes("low")) return cssVar("--severity-low", "#34C759");
  return cssVar("--ios-gray-1", "#8E8E93");
}


function makeTimeTicks(
  bins: DensityBin[],
  cellWidth: number,
): { x: number; label: string }[] {
  if (bins.length === 0) return [];
  const tickPx = 110;
  const binsPerTick = Math.max(1, Math.round(tickPx / cellWidth));
  const ticks: { x: number; label: string }[] = [];
  for (let i = 0; i < bins.length; i += binsPerTick) {
    const d = new Date(bins[i].start);
    const hh = d.getHours().toString().padStart(2, "0");
    const mm = d.getMinutes().toString().padStart(2, "0");
    ticks.push({
      x: i * cellWidth + cellWidth / 2,
      label: `${hh}:${mm}`,
    });
  }
  return ticks;
}


function formatBinRange(isoStart: string, binMinutes: number): string {
  const start = new Date(isoStart);
  const end = new Date(start.getTime() + binMinutes * 60_000);
  const fmt = (d: Date) =>
    `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  const sameDay = new Date().toDateString() === start.toDateString();
  if (sameDay) return `${fmt(start)} – ${fmt(end)}`;
  return `${start.toLocaleDateString()} ${fmt(start)} – ${fmt(end)}`;
}


function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y,     x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x,     y + h, radius);
  ctx.arcTo(x,     y + h, x,     y,     radius);
  ctx.arcTo(x,     y,     x + w, y,     radius);
  ctx.closePath();
}


/** Wrapper that adds a time-window picker. Drop this anywhere. */
export function AlarmDensityHeatmapCard() {
  const [windowHours, setWindowHours] = useState(6);

  const binMinutes = windowHours <= 6 ? 5
                   : windowHours <= 24 ? 15
                   : windowHours <= 72 ? 60
                   : 120;  // up to 1w

  return (
    <SectionCard
      title="Alarm density"
      subtitle="When alarms cluster — hot cells = many activations in that bin"
      action={
        <div
          className="flex gap-0.5 p-0.5 rounded-md"
          style={{ backgroundColor: "var(--ios-gray-5)" }}
        >
          {[6, 24, 72, 168].map((h) => (
            <button
              key={h}
              type="button"
              onClick={() => setWindowHours(h)}
              className="text-[11px] font-medium rounded px-2 py-0.5 transition-colors"
              style={windowHours === h
                ? { backgroundColor: "var(--bg-elevated)", color: "var(--text-primary)" }
                : { color: "var(--text-secondary)" }}
            >
              {h < 24 ? `${h}h` : h === 24 ? "1d" : h === 72 ? "3d" : "1w"}
            </button>
          ))}
        </div>
      }
    >
      <AlarmDensityHeatmap windowHours={windowHours} binMinutes={binMinutes} />
    </SectionCard>
  );
}
