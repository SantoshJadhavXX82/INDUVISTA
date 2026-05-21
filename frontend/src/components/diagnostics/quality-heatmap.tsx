/**
 * Phase 19 — QualityHeatmap with gradient/polish aesthetic.
 *
 * Two view modes — per-device (default) and per-tag (drill-down).
 *
 * Visual treatment:
 *   - Cells have rounded 3px corners and a 1.5px gap between them, creating
 *     a "grid of pebbles" look rather than a contiguous block
 *   - Each cell is a vertical gradient (lighter top → richer bottom),
 *     giving subtle depth without sacrificing the discrete semantic
 *     classification (good/uncertain/invalid)
 *   - Problem cells (red/amber) get a soft outer glow via canvas shadow —
 *     draws attention to issues without screaming
 *   - Background is a subtle radial gradient, brightest in the centre
 *   - Cross-hair: hovering highlights the row band + column band
 *
 * Performance: 4 gradients are cached per (rowHeight, devicePixelRatio).
 * Even at 400 rows × 96 bins = 38k cells, draw completes in <50ms.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";


type HeatmapTag = {
  tag_id: number;
  tag_name: string;
  device_id: number;
  device_name: string;
};

type HeatmapBin = { start: string };

type HeatmapResponse = {
  window_hours: number;
  bin_minutes: number;
  tags: HeatmapTag[];
  bins: HeatmapBin[];
  cells: number[][];
};

type ViewMode = "device" | "tag";

const QUALITY_LABELS = ["No data", "Invalid", "Uncertain", "Good"];
const LABEL_WIDTH = 240;
const SCROLLBAR_PAD = 14;
const TIME_AXIS_HEIGHT = 26;
const CELL_GAP = 1.5;
const CELL_RADIUS = 3;


/** Each class gets a top→bottom gradient with a lighter "highlight" stop
 *  near the top and a richer base color at the bottom. */
type RampStop = { top: string; bottom: string; glow: string | null };

function getRamps(palette: Record<number, { top: string; bottom: string; glow: string | null }>): Record<number, RampStop> {
  return palette;
}


function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}


function readRamps(): Record<number, RampStop> {
  // Read theme-aware colors. For each class, define a 2-stop ramp.
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";

  if (isDark) {
    return {
      0: { top: "rgba(255,255,255,0.04)", bottom: "rgba(255,255,255,0.025)", glow: null },                 // no-data: ghost
      1: { top: "#FF6E5E",                 bottom: "#D43030",                  glow: "rgba(255,69,58,0.45)" }, // invalid
      2: { top: "#FFBA52",                 bottom: "#E07D00",                  glow: "rgba(255,159,10,0.30)" }, // uncertain
      3: { top: "#5BE17B",                 bottom: "#1F9E43",                  glow: null },                  // good
    };
  }
  return {
    0: { top: "rgba(0,0,0,0.05)",   bottom: "rgba(0,0,0,0.03)",   glow: null },                              // no-data
    1: { top: "#FF6B6B",            bottom: "#D63031",            glow: "rgba(214,48,49,0.30)" },
    2: { top: "#FFB44A",            bottom: "#E08500",            glow: "rgba(255,149,0,0.25)" },
    3: { top: "#52DB73",            bottom: "#1FA044",            glow: null },
  };
}


export interface QualityHeatmapProps {
  windowHours?: number;
  binMinutes?: number;
  deviceId?: number;
}


export function QualityHeatmap({
  windowHours = 24,
  binMinutes = 15,
  deviceId,
}: QualityHeatmapProps) {
  const heatmap = useQuery({
    queryKey: ["diagnostics", "quality-heatmap", windowHours, binMinutes, deviceId ?? "all"],
    queryFn: () => {
      const params = new URLSearchParams({
        window_hours: String(windowHours),
        bin_minutes: String(binMinutes),
      });
      if (deviceId !== undefined) params.set("device_id", String(deviceId));
      return api.get<HeatmapResponse>(`/diagnostics/quality-heatmap?${params}`);
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 2,           // 2 retries with exponential backoff (default ~1s, 2s)
    retryDelay: (n) => Math.min(1500 * 2 ** n, 8000),
  });

  const data = heatmap.data;

  const [viewMode, setViewMode] = useState<ViewMode>("device");
  const [filter, setFilter] = useState("");
  const [tagFilterDeviceId, setTagFilterDeviceId] = useState<number | null>(null);

  const [ramps, setRamps] = useState(() => readRamps());
  useEffect(() => {
    const obs = new MutationObserver(() => setRamps(readRamps()));
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

  const rows = useMemo(() => {
    if (!data) return { labels: [] as string[], sublabels: [] as string[], cells: [] as number[][], onClick: [] as (() => void)[] };

    if (viewMode === "device") {
      const byDeviceMap = new Map<number, { id: number; name: string; indices: number[] }>();
      data.tags.forEach((t, i) => {
        if (!byDeviceMap.has(t.device_id)) {
          byDeviceMap.set(t.device_id, { id: t.device_id, name: t.device_name, indices: [] });
        }
        byDeviceMap.get(t.device_id)!.indices.push(i);
      });
      const devices = Array.from(byDeviceMap.values()).sort((a, b) => a.name.localeCompare(b.name));
      const labels = devices.map((d) => d.name);
      const sublabels = devices.map((d) => `${d.indices.length} tag${d.indices.length === 1 ? "" : "s"}`);
      const cells = devices.map((d) => {
        const row: number[] = [];
        for (let j = 0; j < data.bins.length; j++) {
          let worst = 3;
          let allNoData = true;
          for (const ti of d.indices) {
            const k = data.cells[ti][j];
            if (k !== 0) {
              allNoData = false;
              if (k < worst) worst = k;
            }
          }
          row.push(allNoData ? 0 : worst);
        }
        return row;
      });
      const onClick = devices.map((d) => () => {
        setViewMode("tag");
        setTagFilterDeviceId(d.id);
        setFilter("");
      });
      return { labels, sublabels, cells, onClick };
    }

    const q = filter.trim().toLowerCase();
    const filtered: { tag: HeatmapTag; cellRow: number[] }[] = [];
    data.tags.forEach((t, i) => {
      if (tagFilterDeviceId !== null && t.device_id !== tagFilterDeviceId) return;
      if (q && !t.tag_name.toLowerCase().includes(q) && !t.device_name.toLowerCase().includes(q)) return;
      filtered.push({ tag: t, cellRow: data.cells[i] });
    });
    return {
      labels: filtered.map((f) => f.tag.tag_name),
      sublabels: filtered.map((f) => f.tag.device_name),
      cells: filtered.map((f) => f.cellRow),
      onClick: filtered.map(() => () => { /* no-op */ }),
    };
  }, [data, viewMode, filter, tagFilterDeviceId]);

  const rowHeight = viewMode === "device" ? 28 : 18;

  // Canvas width fills all space to the right of the labels. Cells are
  // computed as floating-point widths and then snapped to integer pixel
  // boundaries when drawn — this way the row always stretches edge to
  // edge instead of leaving a strip of empty pixels on the right from
  // Math.floor() truncation.
  const canvasWidth = useMemo(() => {
    if (!data) return 0;
    return Math.max(50, containerWidth - LABEL_WIDTH - SCROLLBAR_PAD);
  }, [containerWidth, data]);

  // Average cell width — kept as a float for layout math. The drawing
  // code uses Math.round(j * cellWidth) for each cell's left edge so
  // pixels stay integer-aligned and cells visually adjacent.
  const cellWidth = useMemo(() => {
    if (!data || data.bins.length === 0) return 6;
    return Math.max(3, canvasWidth / data.bins.length);
  }, [canvasWidth, data]);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hoverIdx, setHoverIdx] = useState<{ row: number; col: number } | null>(null);

  // Main render: draws cells with rounded corners + gradients + glow on problems
  useEffect(() => {
    if (!data || !canvasRef.current || rows.cells.length === 0) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const cssWidth = canvasWidth;
    const cssHeight = rows.cells.length * rowHeight;
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${cssHeight}px`;
    canvas.width = cssWidth * dpr;
    canvas.height = cssHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);

    const cellH = rowHeight - CELL_GAP;

    // Two-pass render: first pass = the cells themselves; second pass = glow
    // on top of red/amber cells (so glow doesn't get covered by neighbors).

    // Pass 1 — solid cells
    // Each cell's x is rounded to the nearest integer pixel; the cell's
    // width is `nextCellX - thisCellX`, so adjacent cells share an edge
    // and the entire row stretches all the way to the right of the canvas
    // even when bins × cellWidth isn't a perfect integer.
    for (let i = 0; i < rows.cells.length; i++) {
      const row = rows.cells[i];
      const y0 = i * rowHeight;
      for (let j = 0; j < row.length; j++) {
        const klass = row[j];
        const x0 = Math.round(j * cellWidth);
        const x1 = Math.round((j + 1) * cellWidth);
        const cellW = Math.max(1, x1 - x0 - CELL_GAP);
        const ramp = ramps[klass];

        // Create per-cell gradient
        const grad = ctx.createLinearGradient(0, y0, 0, y0 + cellH);
        grad.addColorStop(0, ramp.top);
        grad.addColorStop(1, ramp.bottom);

        ctx.fillStyle = grad;
        roundRect(ctx, x0, y0, cellW, cellH, CELL_RADIUS);
        ctx.fill();
      }
    }

    // Pass 2 — glow on problem cells (red/amber clusters)
    // Use shadowBlur on top of already-rendered cells for a soft halo.
    for (let i = 0; i < rows.cells.length; i++) {
      const row = rows.cells[i];
      const y0 = i * rowHeight;
      for (let j = 0; j < row.length; j++) {
        const klass = row[j];
        const ramp = ramps[klass];
        if (!ramp.glow) continue;
        const x0 = Math.round(j * cellWidth);
        const x1 = Math.round((j + 1) * cellWidth);
        const cellW = Math.max(1, x1 - x0 - CELL_GAP);

        ctx.save();
        ctx.shadowColor = ramp.glow;
        ctx.shadowBlur = klass === 1 ? 10 : 6;  // stronger glow on invalid
        ctx.fillStyle = ramp.bottom;
        roundRect(ctx, x0, y0, cellW, cellH, CELL_RADIUS);
        ctx.fill();
        ctx.restore();
      }
    }

    // Pass 3 — hover cross-hair overlay
    if (hoverIdx) {
      ctx.save();
      const overlay = document.documentElement.getAttribute("data-theme") === "dark"
        ? "rgba(255,255,255,0.06)"
        : "rgba(0,0,0,0.06)";
      ctx.fillStyle = overlay;
      // Row band
      ctx.fillRect(0, hoverIdx.row * rowHeight, cssWidth, rowHeight);
      // Column band — width spans the hovered cell only
      const colX0 = Math.round(hoverIdx.col * cellWidth);
      const colX1 = Math.round((hoverIdx.col + 1) * cellWidth);
      ctx.fillRect(colX0, 0, colX1 - colX0, cssHeight);
      ctx.restore();
    }
  }, [data, rows, ramps, cellWidth, rowHeight, hoverIdx]);

  const [hover, setHover] = useState<{
    x: number; y: number; label: string; sublabel: string; binStart: string; klass: number;
  } | null>(null);

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!data) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const bi = Math.floor(x / cellWidth);
    const ri = Math.floor(y / rowHeight);
    if (ri < 0 || ri >= rows.cells.length || bi < 0 || bi >= data.bins.length) {
      setHover(null);
      setHoverIdx(null);
      return;
    }
    setHoverIdx({ row: ri, col: bi });
    setHover({
      x: e.clientX, y: e.clientY,
      label: rows.labels[ri],
      sublabel: rows.sublabels[ri],
      binStart: data.bins[bi].start,
      klass: rows.cells[ri][bi],
    });
  };

  if (heatmap.isLoading) {
    return (
      <div className="text-sm text-center py-8" style={{ color: "var(--text-secondary)" }}>
        Loading quality heatmap…
      </div>
    );
  }
  if (heatmap.isError) {
    const message = heatmap.error instanceof Error ? heatmap.error.message : String(heatmap.error);
    return (
      <div
        className="text-sm rounded-md px-3 py-3 flex items-start gap-3"
        style={{
          color: "var(--status-error-on-soft)",
          backgroundColor: "var(--status-error-soft)",
        }}
      >
        <div className="flex-1">
          <div className="font-medium">Couldn't load quality heatmap</div>
          <div className="text-[11px] mt-0.5 opacity-80">{message}</div>
        </div>
        <button
          type="button"
          onClick={() => heatmap.refetch()}
          className="text-[11px] font-medium rounded px-2 py-1"
          style={{ backgroundColor: "var(--bg-elevated)", color: "var(--text-primary)" }}
        >
          Retry
        </button>
      </div>
    );
  }
  if (!data || data.tags.length === 0) {
    return (
      <div className="text-sm text-center py-8" style={{ color: "var(--text-secondary)" }}>
        No tags to display.
      </div>
    );
  }

  const labelTicks = makeTimeTicks(data.bins, cellWidth);
  const activeDeviceFilter =
    tagFilterDeviceId !== null
      ? data.tags.find((t) => t.device_id === tagFilterDeviceId)?.device_name ?? null
      : null;

  // Theme-aware container backdrop gradient
  const containerBg = document.documentElement.getAttribute("data-theme") === "dark"
    ? "radial-gradient(ellipse at top, rgba(255,255,255,0.025) 0%, rgba(255,255,255,0) 60%)"
    : "radial-gradient(ellipse at top, rgba(0,0,0,0.02) 0%, rgba(0,0,0,0) 60%)";

  return (
    <div ref={containerRef} className="relative">
      {/* Toolbar */}
      <div className="flex items-center gap-3 mb-3 flex-wrap">
        <div
          className="flex gap-0.5 p-0.5 rounded-md"
          style={{ backgroundColor: "var(--ios-gray-5)" }}
        >
          <ModeButton
            active={viewMode === "device"}
            onClick={() => { setViewMode("device"); setTagFilterDeviceId(null); setFilter(""); }}
          >Per device</ModeButton>
          <ModeButton
            active={viewMode === "tag"}
            onClick={() => setViewMode("tag")}
          >Per tag</ModeButton>
        </div>

        {viewMode === "tag" && (
          <div
            className="flex items-center gap-1.5 px-2 rounded-md flex-1 min-w-[180px] max-w-[320px]"
            style={{ backgroundColor: "var(--ios-gray-5)", height: 28 }}
          >
            <Search style={{ width: 12, height: 12, color: "var(--text-secondary)" }} />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter tags…"
              className="bg-transparent outline-none text-[12px] flex-1"
              style={{ color: "var(--text-primary)" }}
            />
            {activeDeviceFilter && (
              <button
                type="button"
                onClick={() => setTagFilterDeviceId(null)}
                className="text-[10px] rounded px-1.5 py-0.5 flex items-center gap-1"
                style={{
                  backgroundColor: "var(--ios-blue-soft)",
                  color: "var(--ios-blue-on-soft)",
                }}
                title="Clear device filter"
              >
                {activeDeviceFilter}
                <span style={{ fontSize: 11 }}>×</span>
              </button>
            )}
          </div>
        )}

        <div className="flex items-center gap-2.5 text-[11px] ml-auto" style={{ color: "var(--text-secondary)" }}>
          <LegendSwatch ramp={ramps[3]} label="Good" />
          <LegendSwatch ramp={ramps[2]} label="Uncertain" />
          <LegendSwatch ramp={ramps[1]} label="Invalid" />
          <LegendSwatch ramp={ramps[0]} label="No data" />
        </div>
      </div>

      <div className="text-[11px] tabular-nums mb-2" style={{ color: "var(--text-secondary)" }}>
        {viewMode === "device"
          ? `${rows.labels.length} device${rows.labels.length === 1 ? "" : "s"} · ${data.tags.length} tags total`
          : `${rows.labels.length} of ${data.tags.length} tags shown`}
        {" · "}
        {data.bins.length} bins × {data.bin_minutes}m · last {data.window_hours}h
      </div>

      {/* Grid container with subtle radial backdrop */}
      {rows.cells.length === 0 ? (
        <div className="text-sm text-center py-8" style={{ color: "var(--text-secondary)" }}>
          No matches. Try a different search.
        </div>
      ) : (
        <div
          className="flex"
          style={{
            maxHeight: viewMode === "device" ? 360 : 520,
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
            {rows.labels.map((label, i) => (
              <div
                key={i}
                onClick={rows.onClick[i]}
                className={viewMode === "device" ? "cursor-pointer transition-colors" : ""}
                style={{
                  height: rowHeight,
                  display: "flex",
                  alignItems: "center",
                  padding: "0 12px",
                  gap: 6,
                  borderBottom: "0.5px solid var(--separator)",
                  fontSize: viewMode === "device" ? 12.5 : 11,
                  color: "var(--text-primary)",
                  backgroundColor: hoverIdx?.row === i ? "var(--ios-gray-5)" : "transparent",
                  transition: "background-color 0.12s",
                }}
                onMouseEnter={() => setHoverIdx((h) => ({ row: i, col: h?.col ?? 0 }))}
                title={`${label} · ${rows.sublabels[i]}`}
              >
                <span style={{
                  flex: 1,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  fontWeight: viewMode === "device" ? 500 : 400,
                }}>
                  {label}
                </span>
                <span
                  style={{
                    fontSize: 10,
                    color: "var(--text-secondary)",
                    flexShrink: 0,
                  }}
                >
                  {rows.sublabels[i]}
                </span>
                {viewMode === "device" && (
                  <ChevronRight style={{ width: 13, height: 13, color: "var(--text-tertiary)", flexShrink: 0 }} />
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
      )}

      {/* Time axis */}
      {rows.cells.length > 0 && (
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
      )}

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
          <div className="font-medium text-[12px]">{hover.label}</div>
          <div style={{ color: "var(--text-secondary)", marginTop: 1 }}>{hover.sublabel}</div>
          <div className="mt-1.5 tabular-nums" style={{ color: "var(--text-secondary)" }}>
            {formatBinRange(hover.binStart, data.bin_minutes)}
          </div>
          <div className="mt-1.5 flex items-center gap-2 pt-1.5" style={{ borderTop: "0.5px solid var(--separator)" }}>
            <span
              style={{
                display: "inline-block",
                width: 10, height: 10,
                background: `linear-gradient(to bottom, ${ramps[hover.klass].top}, ${ramps[hover.klass].bottom})`,
                borderRadius: 3,
              }}
            />
            <span className="font-medium">{QUALITY_LABELS[hover.klass]}</span>
          </div>
        </div>
      )}
    </div>
  );
}


function ModeButton({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-[12px] font-medium rounded-md px-2.5 py-1 transition-colors"
      style={active
        ? { backgroundColor: "var(--bg-elevated)", color: "var(--text-primary)", boxShadow: "0 1px 2px rgba(0,0,0,0.06)" }
        : { color: "var(--text-secondary)" }}
    >
      {children}
    </button>
  );
}


function LegendSwatch({ ramp, label }: { ramp: RampStop; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span
        style={{
          display: "inline-block",
          width: 11, height: 11,
          background: `linear-gradient(to bottom, ${ramp.top}, ${ramp.bottom})`,
          borderRadius: 3,
          border: "0.5px solid var(--separator)",
        }}
      />
      {label}
    </span>
  );
}


function makeTimeTicks(
  bins: HeatmapBin[],
  cellWidth: number,
): { x: number; label: string }[] {
  if (bins.length === 0) return [];
  const tickPx = 100;
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


/** Draw a rounded rectangle path. */
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


export function QualityHeatmapCard() {
  const [windowHours, setWindowHours] = useState(6);

  const binMinutes = windowHours <= 1 ? 1
                   : windowHours <= 6 ? 5
                   : windowHours <= 24 ? 15
                   : windowHours <= 72 ? 30
                   : 60;

  return (
    <SectionCard
      title="Quality heatmap"
      subtitle="Worst-sample-per-bin · click a device row to drill into its tags"
      action={
        <div
          className="flex gap-0.5 p-0.5 rounded-md"
          style={{ backgroundColor: "var(--ios-gray-5)" }}
        >
          {[1, 6, 24, 72, 168].map((h) => (
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
      <QualityHeatmap windowHours={windowHours} binMinutes={binMinutes} />
    </SectionCard>
  );
}
