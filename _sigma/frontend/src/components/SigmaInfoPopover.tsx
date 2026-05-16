/**
 * Hover popover that shows a live bell-curve diagram for a single tag's
 * statistical summary - scaled to the actual mean (μ) and standard
 * deviation (σ) of that tag's observed readings.
 *
 * Renders nothing extra when stats are unavailable; the children are
 * shown plainly in that case.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";

const HOVER_CLOSE_DELAY_MS = 150;

interface Props {
  mean: number | null;
  stddev: number | null;
  observedMin: number | null;
  observedMax: number | null;
  unit: string | null;
  tagName: string;
  children: ReactNode;
}

export default function SigmaInfoPopover({
  mean, stddev, observedMin, observedMax, unit, tagName, children,
}: Props) {
  const [open, setOpen] = useState(false);
  const closeTimerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
  }, []);

  const cancelClose = () => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  const handleEnter = () => {
    cancelClose();
    setOpen(true);
  };

  // Brief grace period so cursor can travel from the trigger across the
  // 8 px gap into the popover (and back) without the popover snapping shut.
  const handleLeave = () => {
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false);
      closeTimerRef.current = null;
    }, HOVER_CLOSE_DELAY_MS);
  };

  // No stats - render children plainly.
  if (mean == null || stddev == null || !isFinite(stddev) || stddev <= 0) {
    return <>{children}</>;
  }

  return (
    <span
      className="relative inline-block"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      <span className="cursor-help underline decoration-dotted decoration-muted-foreground/40 underline-offset-2">
        {children}
      </span>
      {open && (
        <div
          className="absolute bottom-full right-0 mb-2 z-50"
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
        >
          <div className="bg-card border border-border rounded-md shadow-xl">
            <BellCurveDiagram
              mean={mean}
              stddev={stddev}
              observedMin={observedMin}
              observedMax={observedMax}
              unit={unit ?? ""}
              tagName={tagName}
            />
          </div>
        </div>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------

interface DiagramProps {
  mean: number;
  stddev: number;
  observedMin: number | null;
  observedMax: number | null;
  unit: string;
  tagName: string;
}

function BellCurveDiagram({
  mean, stddev, observedMin, observedMax, unit, tagName,
}: DiagramProps) {
  const W = 360, H = 200;
  const PL = 16, PR = 16, PT = 56, PB = 52;
  const plotW = W - PL - PR;
  const plotH = H - PT - PB;

  // X domain spans ±3.5σ around the mean by default. Stretch to include
  // observed min/max when they sit just outside that window, but DON'T
  // chase pathological outliers (>5σ away) - those would squash the curve
  // into an unreadable spike.
  let xMin = mean - 3.5 * stddev;
  let xMax = mean + 3.5 * stddev;
  if (observedMin != null && observedMin >= mean - 5 * stddev && observedMin < xMin) {
    xMin = observedMin - 0.3 * stddev;
  }
  if (observedMax != null && observedMax <= mean + 5 * stddev && observedMax > xMax) {
    xMax = observedMax + 0.3 * stddev;
  }
  const span = xMax - xMin;
  const xToSvg = (x: number) => PL + ((x - xMin) / span) * plotW;
  const baselineY = PT + plotH;

  // Gaussian shape, normalised so peak = 1 (no need for true PDF area scaling)
  const bell = (x: number) => Math.exp(-((x - mean) ** 2) / (2 * stddev * stddev));
  const yToSvg = (y: number) => PT + plotH * (1 - y);

  const N = 120;
  const curvePath = Array.from({ length: N + 1 }, (_, i) => {
    const x = xMin + (i / N) * span;
    return `${i === 0 ? "M" : "L"}${xToSvg(x).toFixed(1)},${yToSvg(bell(x)).toFixed(1)}`;
  }).join(" ");

  // Filled area between the curve and the baseline, from μ-σ to μ+σ.
  const M = 40;
  const bandPath = (() => {
    const pts: string[] = [];
    pts.push(`M${xToSvg(mean - stddev).toFixed(1)},${baselineY}`);
    for (let i = 0; i <= M; i++) {
      const x = (mean - stddev) + (i / M) * (2 * stddev);
      pts.push(`L${xToSvg(x).toFixed(1)},${yToSvg(bell(x)).toFixed(1)}`);
    }
    pts.push(`L${xToSvg(mean + stddev).toFixed(1)},${baselineY}`);
    pts.push("Z");
    return pts.join(" ");
  })();

  const ticks = [-3, -2, -1, 0, 1, 2, 3]
    .map(k => ({
      k,
      x: mean + k * stddev,
      label: k === 0 ? "μ" : (k > 0 ? `+${k}σ` : `${k}σ`),
    }))
    .filter(t => t.x >= xMin - 1e-9 && t.x <= xMax + 1e-9);

  const fmt = (v: number) => {
    if (!isFinite(v)) return "—";
    const a = Math.abs(v);
    if (a >= 100) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    if (a >= 0.01) return v.toFixed(3);
    return v.toExponential(1);
  };

  const unitSuffix = unit ? " " + unit : "";
  const inRange = (v: number) => v >= xMin && v <= xMax;

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block">
      {/* Title + headline stats */}
      <text x={W / 2} y={20} textAnchor="middle" className="fill-foreground text-[11px] font-medium">
        Distribution shape — {tagName}
      </text>
      <text x={W / 2} y={36} textAnchor="middle" className="fill-muted-foreground text-[10px]">
        μ = {fmt(mean)}{unitSuffix} · σ = {fmt(stddev)}{unitSuffix}
      </text>

      {/* ±σ filled band */}
      <path d={bandPath} className="fill-emerald-300/40 dark:fill-emerald-700/40" />

      {/* Bell curve outline */}
      <path
        d={curvePath}
        fill="none"
        className="stroke-emerald-600 dark:stroke-emerald-400"
        strokeWidth="1.5"
      />

      {/* Mean vertical line */}
      <line
        x1={xToSvg(mean)} y1={PT}
        x2={xToSvg(mean)} y2={baselineY}
        className="stroke-emerald-600 dark:stroke-emerald-400"
        strokeWidth="1.2"
        strokeDasharray="2 2"
      />

      {/* X axis baseline */}
      <line
        x1={PL} y1={baselineY}
        x2={W - PR} y2={baselineY}
        className="stroke-border"
        strokeWidth="0.5"
      />

      {/* Ticks - both σ-multiple label and actual value */}
      {ticks.map(({ k, x, label }) => (
        <g key={k}>
          <line
            x1={xToSvg(x)} y1={baselineY}
            x2={xToSvg(x)} y2={baselineY + 3}
            className="stroke-muted-foreground"
            strokeWidth="0.5"
          />
          <text
            x={xToSvg(x)} y={baselineY + 14}
            textAnchor="middle"
            className="fill-muted-foreground text-[9px] tabular-nums"
          >
            {label}
          </text>
          <text
            x={xToSvg(x)} y={baselineY + 26}
            textAnchor="middle"
            className="fill-muted-foreground/70 text-[9px] tabular-nums"
          >
            {fmt(x)}
          </text>
        </g>
      ))}

      {/* Observed extremes as small triangles below the axis */}
      {observedMin != null && inRange(observedMin) && (
        <polygon
          points={`${xToSvg(observedMin)},${baselineY - 5} ${xToSvg(observedMin) - 4},${baselineY + 1} ${xToSvg(observedMin) + 4},${baselineY + 1}`}
          className="fill-orange-500/80"
        >
          <title>Observed min: {fmt(observedMin)}{unitSuffix}</title>
        </polygon>
      )}
      {observedMax != null && inRange(observedMax) && (
        <polygon
          points={`${xToSvg(observedMax)},${baselineY - 5} ${xToSvg(observedMax) - 4},${baselineY + 1} ${xToSvg(observedMax) + 4},${baselineY + 1}`}
          className="fill-orange-500/80"
        >
          <title>Observed max: {fmt(observedMax)}{unitSuffix}</title>
        </polygon>
      )}

      {/* Footer interpretation */}
      <text x={W / 2} y={H - 18} textAnchor="middle" className="fill-muted-foreground text-[9px]">
        Shaded ±σ ≈ 68% of well-behaved data · ▲ = observed extremes
      </text>
      <text x={W / 2} y={H - 5} textAnchor="middle" className="fill-muted-foreground/70 text-[9px]">
        Beyond ±3σ is statistically unusual
      </text>
    </svg>
  );
}
