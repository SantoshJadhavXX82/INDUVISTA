/**
 * Phase 18 — KpiCard
 *
 * The atomic dashboard card for a single tag's current value. iOS-style
 * white rounded surface with:
 *   - Tag label (small uppercase muted)
 *   - Quality indicator dot (right-aligned)
 *   - Large value with subtle unit suffix
 *   - Inline sparkline below
 *   - Optional trend delta (% change)
 *
 * Sparkline is intentionally minimal (no axes, no labels). It's signal,
 * not analysis — the operator clicks through to Trend for detail.
 *
 * Quality semantics (matches your backend):
 *   - good (st >= 128) : green dot
 *   - warn (st 64-127) : amber dot
 *   - error (st < 64)  : red dot
 *   - none             : gray dot (no value yet)
 *
 * Usage:
 *   <KpiCard
 *     label="DENSITY"
 *     value={62}
 *     unit="kg/m³"
 *     points={[20, 22, 18, 19, 21, 23, 20, 22]}
 *     quality="good"
 *     onClick={() => navigate(`/trend?tags=${tagId}`)}
 *   />
 */
import { cn } from "@/lib/utils";
import { formatFloat } from "@/lib/format";

export type Quality = "good" | "warn" | "error" | "none";

const QUALITY_COLOR: Record<Quality, string> = {
  good:  "var(--status-good)",
  warn:  "var(--status-warn)",
  error: "var(--status-error)",
  none:  "var(--ios-gray-3)",
};

const SPARK_STROKE: Record<Quality, string> = {
  good:  "var(--ios-blue)",
  warn:  "var(--ios-orange)",
  error: "var(--ios-red)",
  none:  "var(--ios-gray-3)",
};

export interface KpiCardProps {
  label: string;
  value: number | string | null;
  unit?: string;
  points?: number[];
  quality?: Quality;
  /** Optional trend delta as a percentage, e.g. 3.2 → "+3.2%". */
  deltaPct?: number;
  onClick?: () => void;
  className?: string;
}

export function KpiCard({
  label, value, unit, points, quality = "good",
  deltaPct, onClick, className,
}: KpiCardProps) {
  const stroke = SPARK_STROKE[quality];
  const valueDisplay =
    value === null || value === undefined
      ? "—"
      : typeof value === "number" ? formatFloat(value) : value;

  return (
    <div
      className={cn(
        "flex flex-col gap-1.5",
        onClick && "cursor-pointer transition-transform hover:scale-[1.01] active:scale-[0.99]",
        className,
      )}
      style={{
        backgroundColor: "var(--bg-elevated)",
        borderRadius: "var(--radius-lg-2)",
        padding: "12px 14px",
        border: "0.5px solid var(--card-edge)",
        boxShadow: "var(--card-shadow)",
      }}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === "Enter" || e.key === " ") onClick(); } : undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className="text-[10px] font-medium uppercase tracking-wider truncate"
          style={{ color: "var(--ios-gray-1)" }}
        >
          {label}
        </span>
        <span
          aria-hidden="true"
          className="inline-block rounded-full shrink-0"
          style={{ width: 7, height: 7, backgroundColor: QUALITY_COLOR[quality] }}
          title={`quality: ${quality}`}
        />
      </div>

      <div className="flex items-baseline gap-1.5">
        <span
          className="text-[22px] font-semibold leading-none tabular-nums"
          style={{ letterSpacing: "-0.02em" }}
        >
          {valueDisplay}
        </span>
        {unit && (
          <span
            className="text-[11px] truncate"
            style={{ color: "var(--ios-gray-1)" }}
          >
            {unit}
          </span>
        )}
        {deltaPct !== undefined && (
          <span
            className="ml-auto text-[10px] font-medium tabular-nums"
            style={{
              color: deltaPct >= 0 ? "var(--status-good-on-soft)" : "var(--status-error-on-soft)",
            }}
          >
            {deltaPct >= 0 ? "+" : ""}{deltaPct.toFixed(1)}%
          </span>
        )}
      </div>

      {points && points.length > 1 && (
        <Sparkline points={points} stroke={stroke} />
      )}
    </div>
  );
}

/** Inline mini-sparkline — auto-scales to point range. Renders into a
 *  fixed 100×24 viewBox so the line is crisp at any container width. */
function Sparkline({ points, stroke }: { points: number[]; stroke: string }) {
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const stepX = 100 / (points.length - 1);
  const path = points
    .map((p, i) => {
      const x = i * stepX;
      const y = 22 - ((p - min) / range) * 20;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox="0 0 100 24"
      preserveAspectRatio="none"
      style={{ width: "100%", height: 20, display: "block" }}
      aria-hidden="true"
    >
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
