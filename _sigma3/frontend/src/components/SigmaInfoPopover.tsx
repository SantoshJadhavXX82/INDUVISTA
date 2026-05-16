/**
 * Hover popover with a live bell-curve diagram for a single tag's
 * statistical summary - scaled to the actual mean (μ) and standard
 * deviation (σ) of that tag's observed readings.
 *
 * The popover is rendered through a React Portal at document.body level
 * with `position: fixed`, so it escapes any ancestor `overflow: hidden`
 * (the Card, the Table wrapper, etc.) that would otherwise clip it to
 * a thin strip. Position is computed from the trigger's bounding rect
 * and re-computed on scroll / resize.
 *
 * Features beyond hover-show:
 *  - Pin button - keeps the popover open while inspecting
 *  - Click-through to raw data - "View this tag in the raw data table"
 *    fires onShowInRawTable(tagId)
 *  - Alarm-threshold preview lines at μ±2σ (amber, warning) and μ±3σ
 *    (red, alarm) - draggable in Phase 14
 */
import {
  useCallback, useEffect, useLayoutEffect, useRef, useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Pin, PinOff, X, ExternalLink } from "lucide-react";

const POPOVER_WIDTH = 360;
const POPOVER_GAP = 8;            // px between trigger and popover
const VIEWPORT_PAD = 8;           // px clamp from viewport edges
const HOVER_CLOSE_DELAY_MS = 150;

interface Position { top: number; left: number; }

interface Props {
  tagId: number;
  mean: number | null;
  stddev: number | null;
  observedMin: number | null;
  observedMax: number | null;
  unit: string | null;
  tagName: string;
  onShowInRawTable?: (tagId: number) => void;
  children: ReactNode;
}

export default function SigmaInfoPopover({
  tagId, mean, stddev, observedMin, observedMax, unit, tagName,
  onShowInRawTable, children,
}: Props) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState<Position | null>(null);

  const triggerRef = useRef<HTMLSpanElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const closeTimerRef = useRef<number | null>(null);

  // -------------------- close / pin / cleanup -----------------------------

  useEffect(() => () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
  }, []);

  // Outside-click + Escape dismiss a pinned popover.
  useEffect(() => {
    if (!pinned) return;
    const onMouseDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popoverRef.current?.contains(t)) return;
      if (triggerRef.current?.contains(t)) return;
      setPinned(false);
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPinned(false);
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [pinned]);

  // Reset position state when popover closes so the next open computes fresh.
  useEffect(() => {
    if (!open) setPosition(null);
  }, [open]);

  // -------------------- position computation ------------------------------

  const recomputePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const popover = popoverRef.current;
    if (!trigger || !popover) return;

    const rect = trigger.getBoundingClientRect();
    const popH = popover.offsetHeight;
    const popW = popover.offsetWidth || POPOVER_WIDTH;

    // Default: anchor to the trigger's right edge, place ABOVE
    let left = rect.right - popW;
    let top  = rect.top - popH - POPOVER_GAP;

    // Clamp horizontally inside the viewport
    if (left < VIEWPORT_PAD) left = VIEWPORT_PAD;
    if (left + popW > window.innerWidth - VIEWPORT_PAD) {
      left = window.innerWidth - popW - VIEWPORT_PAD;
    }

    // Flip below the trigger if there isn't room above
    if (top < VIEWPORT_PAD) {
      top = rect.bottom + POPOVER_GAP;
    }
    // Final guard: clamp bottom edge inside viewport
    if (top + popH > window.innerHeight - VIEWPORT_PAD) {
      top = Math.max(VIEWPORT_PAD, window.innerHeight - popH - VIEWPORT_PAD);
    }

    setPosition({ top, left });
  }, []);

  // First positioning pass - runs after popover renders so offsetHeight is real.
  useLayoutEffect(() => {
    if (!open) return;
    recomputePosition();
  }, [open, recomputePosition]);

  // Keep position fresh during scroll / resize while open.
  useEffect(() => {
    if (!open) return;
    const handler = () => recomputePosition();
    // Capture phase so we catch scroll inside any scrollable ancestor too.
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    return () => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    };
  }, [open, recomputePosition]);

  // -------------------- handlers ------------------------------------------

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

  // Pinned popover ignores mouseleave - only closes via close button,
  // outside-click, or Escape.
  const handleLeave = () => {
    if (pinned) return;
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false);
      closeTimerRef.current = null;
    }, HOVER_CLOSE_DELAY_MS);
  };

  const handleTogglePin = (e: React.MouseEvent) => {
    e.stopPropagation();
    setPinned((p) => !p);
  };

  const handleClose = (e: React.MouseEvent) => {
    e.stopPropagation();
    setPinned(false);
    setOpen(false);
  };

  const handleShowRaw = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onShowInRawTable) return;
    onShowInRawTable(tagId);
    setPinned(false);
    setOpen(false);
  };

  // No stats - render children plainly.
  if (mean == null || stddev == null || !isFinite(stddev) || stddev <= 0) {
    return <>{children}</>;
  }

  // -------------------- render --------------------------------------------

  const popoverNode = open && (
    <div
      ref={popoverRef}
      // Until measured, render off-screen at opacity 0 so we never flash
      // at a wrong position.
      style={{
        position: "fixed",
        top:     position?.top  ?? -9999,
        left:    position?.left ?? 0,
        width:   POPOVER_WIDTH,
        opacity: position ? 1 : 0,
        zIndex:  60,
      }}
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      <div
        className={`bg-card border rounded-md shadow-xl overflow-hidden ${
          pinned ? "border-blue-400 ring-1 ring-blue-200" : "border-border"
        }`}
      >
        {/* Pin / close header */}
        <div className="flex items-center justify-between px-2 py-1 border-b border-border bg-secondary/30">
          {pinned ? (
            <span className="flex items-center gap-1 text-[10px] text-blue-600 font-medium">
              <Pin className="h-3 w-3 fill-blue-600 text-blue-600" />
              Pinned — Esc or click outside to close
            </span>
          ) : (
            <span className="text-[10px] text-muted-foreground">
              Hover-only · pin to interact
            </span>
          )}
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={handleTogglePin}
              onMouseDown={(e) => e.stopPropagation()}
              className={`p-1 rounded hover:bg-secondary transition-colors ${
                pinned ? "text-blue-600" : "text-muted-foreground hover:text-foreground"
              }`}
              title={pinned ? "Unpin" : "Pin to keep open"}
              aria-label={pinned ? "Unpin tooltip" : "Pin tooltip"}
            >
              {pinned ? <PinOff className="h-3 w-3" /> : <Pin className="h-3 w-3" />}
            </button>
            {pinned && (
              <button
                type="button"
                onClick={handleClose}
                onMouseDown={(e) => e.stopPropagation()}
                className="p-1 rounded hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors"
                title="Close"
                aria-label="Close tooltip"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
        </div>

        <BellCurveDiagram
          mean={mean}
          stddev={stddev}
          observedMin={observedMin}
          observedMax={observedMax}
          unit={unit ?? ""}
          tagName={tagName}
        />

        {onShowInRawTable && (
          <div className="border-t border-border bg-secondary/20 px-3 py-1.5">
            <button
              type="button"
              onClick={handleShowRaw}
              onMouseDown={(e) => e.stopPropagation()}
              disabled={!pinned}
              className="flex items-center gap-1.5 text-[11px] text-blue-600 hover:text-blue-800 hover:underline disabled:opacity-50 disabled:cursor-not-allowed disabled:no-underline disabled:text-muted-foreground"
              title={pinned
                ? "Scroll to this tag's rows in the raw data table"
                : "Pin the popover first to enable this button"}
            >
              <ExternalLink className="h-3 w-3" />
              View this tag in the raw data table
            </button>
          </div>
        )}
      </div>
    </div>
  );

  return (
    <span
      ref={triggerRef}
      className="relative inline-block"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      <span className="cursor-help underline decoration-dotted decoration-muted-foreground/40 underline-offset-2">
        {children}
      </span>
      {popoverNode && typeof document !== "undefined"
        ? createPortal(popoverNode, document.body)
        : null}
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
  const W = 360, H = 220;
  const PL = 16, PR = 16, PT = 56, PB = 72;
  const plotW = W - PL - PR;
  const plotH = H - PT - PB;

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

  const bell = (x: number) => Math.exp(-((x - mean) ** 2) / (2 * stddev * stddev));
  const yToSvg = (y: number) => PT + plotH * (1 - y);

  const N = 120;
  const curvePath = Array.from({ length: N + 1 }, (_, i) => {
    const x = xMin + (i / N) * span;
    return `${i === 0 ? "M" : "L"}${xToSvg(x).toFixed(1)},${yToSvg(bell(x)).toFixed(1)}`;
  }).join(" ");

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

  const warningLow  = mean - 2 * stddev;
  const warningHigh = mean + 2 * stddev;
  const alarmLow    = mean - 3 * stddev;
  const alarmHigh   = mean + 3 * stddev;

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block">
      <text x={W / 2} y={20} textAnchor="middle" className="fill-foreground text-[11px] font-medium">
        Distribution shape — {tagName}
      </text>
      <text x={W / 2} y={36} textAnchor="middle" className="fill-muted-foreground text-[10px]">
        μ = {fmt(mean)}{unitSuffix} · σ = {fmt(stddev)}{unitSuffix}
      </text>

      <path d={bandPath} className="fill-emerald-300/40 dark:fill-emerald-700/40" />

      {inRange(warningLow) && (
        <line x1={xToSvg(warningLow)} y1={PT} x2={xToSvg(warningLow)} y2={baselineY}
              className="stroke-amber-500" strokeWidth="0.8" strokeDasharray="3 2" opacity="0.6"/>
      )}
      {inRange(warningHigh) && (
        <line x1={xToSvg(warningHigh)} y1={PT} x2={xToSvg(warningHigh)} y2={baselineY}
              className="stroke-amber-500" strokeWidth="0.8" strokeDasharray="3 2" opacity="0.6"/>
      )}
      {inRange(alarmLow) && (
        <line x1={xToSvg(alarmLow)} y1={PT} x2={xToSvg(alarmLow)} y2={baselineY}
              className="stroke-red-500" strokeWidth="0.8" strokeDasharray="3 2" opacity="0.6"/>
      )}
      {inRange(alarmHigh) && (
        <line x1={xToSvg(alarmHigh)} y1={PT} x2={xToSvg(alarmHigh)} y2={baselineY}
              className="stroke-red-500" strokeWidth="0.8" strokeDasharray="3 2" opacity="0.6"/>
      )}

      <path d={curvePath} fill="none"
            className="stroke-emerald-600 dark:stroke-emerald-400" strokeWidth="1.5"/>

      <line x1={xToSvg(mean)} y1={PT} x2={xToSvg(mean)} y2={baselineY}
            className="stroke-emerald-600 dark:stroke-emerald-400"
            strokeWidth="1.2" strokeDasharray="2 2"/>

      <line x1={PL} y1={baselineY} x2={W - PR} y2={baselineY}
            className="stroke-border" strokeWidth="0.5"/>

      {ticks.map(({ k, x, label }) => (
        <g key={k}>
          <line x1={xToSvg(x)} y1={baselineY} x2={xToSvg(x)} y2={baselineY + 3}
                className="stroke-muted-foreground" strokeWidth="0.5"/>
          <text x={xToSvg(x)} y={baselineY + 14} textAnchor="middle"
                className="fill-muted-foreground text-[9px] tabular-nums">
            {label}
          </text>
          <text x={xToSvg(x)} y={baselineY + 26} textAnchor="middle"
                className="fill-muted-foreground/70 text-[9px] tabular-nums">
            {fmt(x)}
          </text>
        </g>
      ))}

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

      <g transform={`translate(${PL}, ${H - 28})`}>
        <line x1="0" y1="3" x2="14" y2="3" className="stroke-amber-500" strokeWidth="1.2" strokeDasharray="3 2" opacity="0.7"/>
        <text x="18" y="6" className="fill-muted-foreground text-[9px]">warn ±2σ</text>
        <line x1="70" y1="3" x2="84" y2="3" className="stroke-red-500" strokeWidth="1.2" strokeDasharray="3 2" opacity="0.7"/>
        <text x="88" y="6" className="fill-muted-foreground text-[9px]">alarm ±3σ</text>
        <text x="146" y="6" className="fill-muted-foreground/60 text-[8px]">(draggable in Phase 14)</text>
      </g>

      <text x={W / 2} y={H - 8} textAnchor="middle" className="fill-muted-foreground/80 text-[9px]">
        Shaded ±σ ≈ 68% · ▲ = observed extremes
      </text>
    </svg>
  );
}
