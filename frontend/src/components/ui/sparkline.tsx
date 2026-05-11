/**
 * Tiny SVG sparkline — no chart library, no animation overhead.
 * Renders a polyline scaled to fit the given width × height.
 *
 * The path auto-rescales to the visible Y-range so even tags with very
 * narrow value ranges show a meaningful shape. A flat line (single
 * value) renders as a horizontal line at mid-height.
 */
import { useMemo } from "react";

type Point = { time: string; value: number };

type Props = {
  points: Point[];
  width?: number;
  height?: number;
  /** Stroke color — defaults to current text color for theme adaptation. */
  stroke?: string;
};

export function Sparkline({
  points,
  width = 120,
  height = 28,
  stroke = "currentColor",
}: Props) {
  const path = useMemo(() => {
    if (!points || points.length === 0) return null;
    if (points.length === 1) {
      // single point → flat midline
      return `M 0 ${height / 2} L ${width} ${height / 2}`;
    }
    const values = points.map((p) => p.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;  // avoid divide-by-zero on flat series
    const padding = 2;
    const yMin = padding;
    const yMax = height - padding;
    const xStep = points.length > 1 ? width / (points.length - 1) : 0;
    return points
      .map((p, i) => {
        const x = i * xStep;
        const y = yMax - ((p.value - min) / range) * (yMax - yMin);
        return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join(" ");
  }, [points, width, height]);

  if (!path) {
    return (
      <svg width={width} height={height} className="opacity-30">
        <line x1="0" y1={height / 2} x2={width} y2={height / 2}
              stroke={stroke} strokeDasharray="2 3" strokeWidth="0.5" />
      </svg>
    );
  }

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.25"
            strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
