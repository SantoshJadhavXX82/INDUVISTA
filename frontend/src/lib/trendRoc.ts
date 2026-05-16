/**
 * Phase 13.12 — Rate-of-change computation for the Trend module.
 *
 * Computes a signed slope at the trailing edge of a sample series using
 * least-squares linear regression. Least-squares is used in preference
 * to a simple first-vs-last finite difference because Modbus polling
 * jitter makes the latter visibly noisy; the regression line averages
 * out the dither while staying sensitive to genuine trend changes.
 *
 * Window:
 *   - Last 20 samples OR last 5 minutes of the visible range,
 *     whichever produces the smaller window
 *   - Minimum 3 GOOD-quality samples required (returns isValid=false otherwise)
 *
 * Quality filter:
 *   - ST byte: GOOD is >= 128. This matches the convention used
 *     throughout InduVista (see QualityFilterSelector.tsx,
 *     TrendChart.tsx, TrendTooltip.tsx, Dashboard.tsx, TagExplorer.tsx,
 *     tag-quality-badge.tsx — all gate on ST_READ_OK = 128). The
 *     128-191 band is treated as "good with minor flags" per
 *     help-text.ts §quality.
 *   - Samples without a `q` field are assumed GOOD (frontend-only
 *     buffers don't always carry quality).
 *
 * Ordering:
 *   - The /trends/history endpoint does NOT guarantee ascending order
 *     (TrendChart sorts X-axis defensively at line ~561). computeROC
 *     does the same: it finds max(t), filters to the trailing window,
 *     then sorts ascending before fitting.
 */

export type RocUnit = "/s" | "/min" | "/hr";

export interface RocSample {
  /** Sample timestamp in milliseconds since epoch. */
  t: number;
  /** Engineering-unit value. */
  v: number;
  /** ST byte. Optional; if absent, sample is treated as GOOD. */
  q?: number;
}

export interface RocResult {
  /** Signed slope in EU per chosen unit. Zero if isValid is false. */
  value: number;
  /** Unit the slope is reported in. */
  unit: RocUnit;
  /** Number of GOOD samples used in the fit. */
  samplesUsed: number;
  /** Total samples present in the trailing window before quality
      filtering. Lets the caller distinguish "fetch returned nothing"
      (0) from "fetch returned data but all BAD" (>0, samplesUsed=0). */
  totalInWindow: number;
  /** Span of the fit window in seconds. */
  windowSec: number;
  /** False when too few good samples are present or denominator is zero. */
  isValid: boolean;
}

const UNIT_TO_MS: Record<RocUnit, number> = {
  "/s": 1_000,
  "/min": 60_000,
  "/hr": 3_600_000,
};

const MAX_SAMPLES = 20;
const MAX_WINDOW_MS = 5 * 60 * 1_000;
const MIN_GOOD_SAMPLES = 3;

/**
 * InduVista-wide GOOD threshold. ST >= 128 is acceptable for reporting
 * (matches ST_READ_OK constant used by Dashboard, TagExplorer, the
 * Trend quality filter, tag badges, and the chart's hover tooltip).
 */
const GOOD_THRESHOLD = 128;

/**
 * Compute the rate-of-change at the trailing edge of `samples`.
 *
 * Defensive against unordered input: finds max(t), filters samples to
 * `[maxT - 5min, maxT]`, sorts ascending, then applies the quality
 * filter and walks backward up to MAX_SAMPLES.
 *
 * Returns `isValid: false` if fewer than 3 GOOD samples are present
 * in the trailing window. `totalInWindow` reflects all samples in the
 * trailing window before quality filtering, regardless of validity.
 */
export function computeROC(
  samples: readonly RocSample[] | null | undefined,
  unit: RocUnit = "/min",
): RocResult {
  const empty: RocResult = {
    value: 0,
    unit,
    samplesUsed: 0,
    totalInWindow: 0,
    windowSec: 0,
    isValid: false,
  };

  if (!samples || samples.length === 0) return empty;

  // Find the most recent timestamp without assuming sort order.
  let lastT = -Infinity;
  for (const s of samples) {
    if (s.t > lastT) lastT = s.t;
  }
  if (!isFinite(lastT)) return empty;
  const windowFloor = lastT - MAX_WINDOW_MS;

  // Collect everything in the trailing window. Track total separately
  // from the post-quality-filter count so callers can build a helpful
  // diagnostic tooltip.
  const inWindow: RocSample[] = [];
  for (const s of samples) {
    if (s.t < windowFloor) continue;
    inWindow.push(s);
  }
  // Defensive sort — chart code does the same; API order isn't
  // guaranteed (see TrendChart.tsx line ~561).
  inWindow.sort((a, b) => a.t - b.t);

  // Apply quality + finiteness filter, walking backward from the end
  // so the most-recent samples win the MAX_SAMPLES cap.
  const good: RocSample[] = [];
  for (let i = inWindow.length - 1; i >= 0; i--) {
    const s = inWindow[i];
    if (s.q !== undefined && s.q < GOOD_THRESHOLD) continue;
    if (!Number.isFinite(s.v)) continue;
    good.unshift(s);
    if (good.length >= MAX_SAMPLES) break;
  }

  if (good.length < MIN_GOOD_SAMPLES) {
    return { ...empty, totalInWindow: inWindow.length };
  }

  // Least-squares slope, normalised by t0 for numerical stability
  // (raw epoch ms squared overflows JS number precision for small spans).
  const t0 = good[0].t;
  const n = good.length;
  let sumX = 0;
  let sumY = 0;
  let sumXY = 0;
  let sumX2 = 0;
  for (const s of good) {
    const x = s.t - t0;
    const y = s.v;
    sumX += x;
    sumY += y;
    sumXY += x * y;
    sumX2 += x * x;
  }
  const denom = n * sumX2 - sumX * sumX;
  if (denom === 0) {
    return { ...empty, totalInWindow: inWindow.length };
  }

  const slopePerMs = (n * sumXY - sumX * sumY) / denom;
  const value = slopePerMs * UNIT_TO_MS[unit];

  return {
    value,
    unit,
    samplesUsed: n,
    totalInWindow: inWindow.length,
    windowSec: (good[n - 1].t - good[0].t) / 1000,
    isValid: true,
  };
}

/**
 * Render an RocResult for display.
 *
 *   formatROC({ value: 0.42, ..., isValid: true }, "°C")  →  "+0.42 °C/min"
 *   formatROC({ value: -125, ..., isValid: true }, "bar") →  "-125 bar/min"
 *   formatROC({ value: 0,    ..., isValid: false }, "kg") →  "—"
 *
 * Decimal count auto-scales with magnitude so values stay legible
 * across the wide dynamic range of plant signals.
 */
export function formatROC(r: RocResult, eu?: string | null): string {
  if (!r.isValid) return "—";

  const abs = Math.abs(r.value);
  let decimals: number;
  if (abs >= 100) decimals = 0;
  else if (abs >= 10) decimals = 1;
  else if (abs >= 0.01 || abs === 0) decimals = 2;
  else decimals = 4;

  const prefix = r.value > 0 ? "+" : ""; // negatives already get "-" from toFixed
  const num = r.value.toFixed(decimals);
  const euStr = (eu ?? "").trim();
  return `${prefix}${num} ${euStr}${r.unit}`;
}

/**
 * Build a diagnostic tooltip for the ROC cell. Reveals what the
 * compute actually saw: how many samples in the trailing window,
 * how many were GOOD-quality, and why it returned invalid if so.
 */
export function rocTooltip(r: RocResult): string {
  if (r.isValid) {
    return `${r.samplesUsed} good samples over ${r.windowSec.toFixed(0)}s (of ${r.totalInWindow} in trailing 5 min)`;
  }
  if (r.totalInWindow === 0) {
    return "Trailing 5-min window returned no samples";
  }
  if (r.samplesUsed === 0) {
    return `${r.totalInWindow} samples in trailing 5 min, 0 GOOD (all filtered — ST < ${GOOD_THRESHOLD})`;
  }
  return `${r.totalInWindow} samples in trailing 5 min, ${r.samplesUsed} good — need at least ${MIN_GOOD_SAMPLES}`;
}

// ---------------------------------------------------------------------------
// Unit selector option list and localStorage helpers
// ---------------------------------------------------------------------------

export const ROC_UNITS: ReadonlyArray<{ value: RocUnit; label: string }> = [
  { value: "/s", label: "/s" },
  { value: "/min", label: "/min" },
  { value: "/hr", label: "/hr" },
] as const;

export const ROC_UNIT_STORAGE_KEY = "induvista.rocUnit";
const DEFAULT_UNIT: RocUnit = "/min";

export function loadRocUnit(): RocUnit {
  try {
    const stored = localStorage.getItem(ROC_UNIT_STORAGE_KEY);
    if (stored === "/s" || stored === "/min" || stored === "/hr") {
      return stored;
    }
  } catch {
    // localStorage may be unavailable (SSR, private mode); fall through.
  }
  return DEFAULT_UNIT;
}

export function saveRocUnit(unit: RocUnit): void {
  try {
    localStorage.setItem(ROC_UNIT_STORAGE_KEY, unit);
  } catch {
    // ignore
  }
}
