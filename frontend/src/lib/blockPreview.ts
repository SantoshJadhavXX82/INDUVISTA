/**
 * Phase 17.0b Chunk B - JS preview engine for stateless calc blocks.
 *
 * Mirrors the Python implementations in app/workers/calc_blocks/. Each
 * stateless block has a JS evaluator that takes the same (config, samples)
 * inputs and returns a BlockResult matching what the worker would produce.
 *
 * Stateful blocks (TON, TOF, etc.) are NOT in this file - they're delegated
 * to the backend /api/computed-tags/preview endpoint via STATEFUL_BLOCK_CODES.
 *
 * Edge cases mirrored from Python:
 *   - DIV/MOD by zero -> BAD
 *   - SQRT of negative -> BAD
 *   - LN/LOG10 of non-positive -> BAD
 *   - POW returning complex or non-finite -> BAD
 *   - GEOMETRIC_MEAN with non-positive input -> BAD
 *   - HARMONIC_MEAN with zero input -> BAD
 *   - TAN near asymptotes (|result| > 1e15) -> BAD
 *   - Any aggregation with worst input < GOOD -> BAD (Tier A policy)
 *   - ROUND uses banker's rounding (matches Python's round())
 *
 * Floating-point semantics may diverge slightly from Python on edge cases
 * (subnormals, tiny rounding differences) - acceptable for preview.
 */

// ===========================================================================
// Types and constants
// ===========================================================================

export const GOOD_QUALITY = 128;
export const GOOD_NON_SPECIFIC = 192;
export const BAD_QUALITY = 0;


export interface InputSample {
  tag_id: number;
  value: number | null;
  quality: number;
}

export interface BlockResult {
  value: number | null;
  quality: number;
}

export interface PreviewResult extends BlockResult {
  status: "ok" | "validation_error" | "execution_error" | "unknown_block" | "stateful_deferred";
  error?: string | null;
}


/** Block codes whose evaluation requires server-side state. Caller must
 *  delegate to /api/computed-tags/preview for these. */
export const STATEFUL_BLOCK_CODES = new Set<string>([
  "TON", "TOF", "TP",
  "R_TRIG", "F_TRIG",
  "SR", "RS",
  "CTU", "CTD",
  // Add others as discovered in stateful_tier_d.py
]);


export function isStateful(code: string): boolean {
  return STATEFUL_BLOCK_CODES.has(code);
}


// ===========================================================================
// Helpers (mirror Python helpers from base.py and the tier files)
// ===========================================================================

function worstQuality(samples: InputSample[]): number {
  if (samples.length === 0) return BAD_QUALITY;
  return Math.min(...samples.map(s => s.quality));
}

function allGood(samples: InputSample[]): boolean {
  return samples.every(s => s.quality >= GOOD_QUALITY && s.value !== null);
}

/** Aggregation policy: GOOD_NON_SPECIFIC if all good, else worst. */
function aggOutputQuality(samples: InputSample[]): number {
  const w = worstQuality(samples);
  return w >= GOOD_QUALITY ? GOOD_NON_SPECIFIC : w;
}

/** Extract numeric values from samples (skip nulls). */
function goodValues(samples: InputSample[]): number[] {
  const out: number[] = [];
  for (const s of samples) if (s.value !== null) out.push(s.value);
  return out;
}

/** Aggregation strict mode: returns ([], worst) if any input is < GOOD.
 *  Matches arithmetic_tier_e's _nary_good_values. */
function naryGoodValues(samples: InputSample[]): { vals: number[]; quality: number } {
  if (samples.length === 0) return { vals: [], quality: 0 };
  const w = worstQuality(samples);
  if (w < GOOD_QUALITY) return { vals: [], quality: w };
  const vals: number[] = [];
  for (const s of samples) if (s.value !== null) vals.push(s.value);
  return { vals, quality: GOOD_NON_SPECIFIC };
}

function isNaryMode(cfg: any): boolean {
  return cfg && typeof cfg === "object" && "inputs" in cfg;
}

/** Resolve binary block (left + right_tag or left + value_const).
 *  Returns null on BAD input - caller emits BAD output. */
function binaryOperands(
  cfg: any, samples: InputSample[],
): { left: number; right: number; quality: number } | { left: null; right: null; quality: number } {
  const ls = samples[0];
  if (ls.quality < GOOD_QUALITY || ls.value === null) {
    return { left: null, right: null, quality: ls.quality };
  }
  if ("right" in cfg) {
    const rs = samples[1];
    if (rs.quality < GOOD_QUALITY || rs.value === null) {
      return { left: null, right: null, quality: rs.quality };
    }
    return { left: ls.value, right: rs.value, quality: GOOD_NON_SPECIFIC };
  }
  return { left: ls.value, right: Number(cfg.value), quality: GOOD_NON_SPECIFIC };
}

function unaryOperand(samples: InputSample[]): { v: number; quality: number } | { v: null; quality: number } {
  const s = samples[0];
  if (s.quality < GOOD_QUALITY || s.value === null) return { v: null, quality: s.quality };
  return { v: s.value, quality: GOOD_NON_SPECIFIC };
}

/** Resolve N-ary mixed items (tag refs + constants).
 *  Returns null on BAD tag sample. */
function resolveNaryOperands(
  items: Array<{ tag?: number; value?: number }>, samples: InputSample[],
): { ops: number[]; quality: number } | { ops: null; quality: number } {
  let sampleIdx = 0;
  const ops: number[] = [];
  for (const item of items) {
    if ("value" in item && item.value !== undefined) {
      ops.push(Number(item.value));
    } else {
      const s = samples[sampleIdx++];
      if (!s) return { ops: null, quality: 0 };
      if (s.quality < GOOD_QUALITY || s.value === null) {
        return { ops: null, quality: s.quality };
      }
      ops.push(s.value);
    }
  }
  return { ops, quality: GOOD_NON_SPECIFIC };
}

/** Python-style banker's rounding (half-to-even). */
function bankerRound(x: number): number {
  const r = Math.round(x);
  const diff = Math.abs(x - Math.floor(x) - 0.5);
  if (diff < 1e-10) {
    const fl = Math.floor(x);
    return fl + (fl % 2 === 0 ? 0 : 1);
  }
  return r;
}

function median(vals: number[]): number {
  const sorted = [...vals].sort((a, b) => a - b);
  const n = sorted.length;
  if (n === 0) return NaN;
  return n % 2 === 0
    ? (sorted[n / 2 - 1] + sorted[n / 2]) / 2
    : sorted[Math.floor(n / 2)];
}

function variance(vals: number[]): number {
  // Sample variance, n-1 divisor (Bessel correction). Matches statistics.variance.
  const n = vals.length;
  if (n < 2) return NaN;
  const mean = vals.reduce((a, b) => a + b, 0) / n;
  const sq = vals.reduce((a, b) => a + (b - mean) ** 2, 0);
  return sq / (n - 1);
}

function stdev(vals: number[]): number {
  return Math.sqrt(variance(vals));
}

function mode(vals: number[]): number {
  // Python statistics.multimode()[0] - smallest of the most frequent.
  const counts = new Map<number, number>();
  for (const v of vals) counts.set(v, (counts.get(v) ?? 0) + 1);
  let maxCount = 0;
  for (const c of counts.values()) if (c > maxCount) maxCount = c;
  const modes: number[] = [];
  for (const [v, c] of counts) if (c === maxCount) modes.push(v);
  return Math.min(...modes);
}


// ===========================================================================
// Aggregation Tier A (15 blocks)
// ===========================================================================

function evalAvgOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  return { value: vals.reduce((a, b) => a + b, 0) / vals.length, quality: aggOutputQuality(samples) };
}

function evalMinOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  return { value: Math.min(...vals), quality: aggOutputQuality(samples) };
}

function evalMaxOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  return { value: Math.max(...vals), quality: aggOutputQuality(samples) };
}

function evalMedianOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  return { value: median(vals), quality: aggOutputQuality(samples) };
}

function evalModeOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  return { value: mode(vals), quality: aggOutputQuality(samples) };
}

function evalRangeOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  return { value: Math.max(...vals) - Math.min(...vals), quality: aggOutputQuality(samples) };
}

function evalStddevOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (vals.length < 2) return { value: null, quality: aggOutputQuality(samples) };
  return { value: stdev(vals), quality: aggOutputQuality(samples) };
}

function evalVarianceOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (vals.length < 2) return { value: null, quality: aggOutputQuality(samples) };
  return { value: variance(vals), quality: aggOutputQuality(samples) };
}

function evalRmsOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  const meanSq = vals.reduce((a, b) => a + b * b, 0) / vals.length;
  return { value: Math.sqrt(meanSq), quality: aggOutputQuality(samples) };
}

function evalProductOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  return { value: vals.reduce((a, b) => a * b, 1), quality: aggOutputQuality(samples) };
}

function evalGeometricMean(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  if (vals.some(v => v <= 0)) return { value: null, quality: 0 };
  const sumLog = vals.reduce((a, b) => a + Math.log(b), 0);
  return { value: Math.exp(sumLog / vals.length), quality: aggOutputQuality(samples) };
}

function evalHarmonicMean(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(samples) };
  if (vals.some(v => v === 0)) return { value: null, quality: 0 };
  return { value: vals.length / vals.reduce((a, b) => a + 1 / b, 0), quality: aggOutputQuality(samples) };
}

function evalWeightedAvg(cfg: any, samples: InputSample[]): BlockResult {
  const weights = cfg.weights as number[];
  const paired: Array<[number, number]> = [];
  samples.forEach((s, i) => {
    if (s.value !== null && weights[i] !== undefined) paired.push([s.value, weights[i]]);
  });
  if (!paired.length) return { value: null, quality: aggOutputQuality(samples) };
  const ws = paired.reduce((a, [v, w]) => a + v * w, 0);
  const wt = paired.reduce((a, [, w]) => a + w, 0);
  return { value: ws / wt, quality: aggOutputQuality(samples) };
}

function evalCountGood(cfg: any, samples: InputSample[]): BlockResult {
  const c = samples.filter(s => s.quality >= GOOD_QUALITY).length;
  return { value: c, quality: GOOD_NON_SPECIFIC };
}

function evalCountNonzero(cfg: any, samples: InputSample[]): BlockResult {
  const c = samples.filter(s => s.value !== null && s.value !== 0).length;
  return { value: c, quality: GOOD_NON_SPECIFIC };
}


// ===========================================================================
// Arithmetic Tier E - Binary (8) with ADD/MUL N-ary support
// ===========================================================================

function evalAdd(cfg: any, samples: InputSample[]): BlockResult {
  if (isNaryMode(cfg)) {
    const r = resolveNaryOperands(cfg.inputs, samples);
    if (r.ops === null) return { value: null, quality: r.quality };
    return { value: r.ops.reduce((a, b) => a + b, 0), quality: r.quality };
  }
  const { left, right, quality } = binaryOperands(cfg, samples);
  if (left === null) return { value: null, quality };
  return { value: left + (right as number), quality };
}

function evalSub(cfg: any, samples: InputSample[]): BlockResult {
  const { left, right, quality } = binaryOperands(cfg, samples);
  if (left === null) return { value: null, quality };
  return { value: left - (right as number), quality };
}

function evalMul(cfg: any, samples: InputSample[]): BlockResult {
  if (isNaryMode(cfg)) {
    const r = resolveNaryOperands(cfg.inputs, samples);
    if (r.ops === null) return { value: null, quality: r.quality };
    return { value: r.ops.reduce((a, b) => a * b, 1), quality: r.quality };
  }
  const { left, right, quality } = binaryOperands(cfg, samples);
  if (left === null) return { value: null, quality };
  return { value: left * (right as number), quality };
}

function evalDiv(cfg: any, samples: InputSample[]): BlockResult {
  const { left, right, quality } = binaryOperands(cfg, samples);
  if (left === null) return { value: null, quality };
  if (right === 0) return { value: null, quality: 0 };
  return { value: left / (right as number), quality };
}

function evalMod(cfg: any, samples: InputSample[]): BlockResult {
  // math.fmod sign follows dividend. JS % does too.
  const { left, right, quality } = binaryOperands(cfg, samples);
  if (left === null) return { value: null, quality };
  if (right === 0) return { value: null, quality: 0 };
  return { value: left % (right as number), quality };
}

function evalPow(cfg: any, samples: InputSample[]): BlockResult {
  const { left, right, quality } = binaryOperands(cfg, samples);
  if (left === null) return { value: null, quality };
  const v = Math.pow(left, right as number);
  if (!Number.isFinite(v) || Number.isNaN(v)) return { value: null, quality: 0 };
  return { value: v, quality };
}

function evalMinOfTwo(cfg: any, samples: InputSample[]): BlockResult {
  const { left, right, quality } = binaryOperands(cfg, samples);
  if (left === null) return { value: null, quality };
  return { value: Math.min(left, right as number), quality };
}

function evalMaxOfTwo(cfg: any, samples: InputSample[]): BlockResult {
  const { left, right, quality } = binaryOperands(cfg, samples);
  if (left === null) return { value: null, quality };
  return { value: Math.max(left, right as number), quality };
}


// ===========================================================================
// Arithmetic Tier E - Unary math (6) + Transcendental (6)
// ===========================================================================

function evalAbs(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  return { value: Math.abs(v), quality };
}

function evalNeg(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  return { value: -v, quality };
}

function evalSqrt(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  if (v < 0) return { value: null, quality: 0 };
  return { value: Math.sqrt(v), quality };
}

function evalFloor(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  return { value: Math.floor(v), quality };
}

function evalCeil(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  return { value: Math.ceil(v), quality };
}

function evalRound(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  return { value: bankerRound(v), quality };
}

function evalExp(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  const r = Math.exp(v);
  if (!Number.isFinite(r)) return { value: null, quality: 0 };
  return { value: r, quality };
}

function evalLn(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  if (v <= 0) return { value: null, quality: 0 };
  return { value: Math.log(v), quality };
}

function evalLog10(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  if (v <= 0) return { value: null, quality: 0 };
  return { value: Math.log10(v), quality };
}

function evalSin(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  return { value: Math.sin(v), quality };
}

function evalCos(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  return { value: Math.cos(v), quality };
}

function evalTan(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(samples);
  if (v === null) return { value: null, quality };
  const r = Math.tan(v);
  if (!Number.isFinite(r) || Math.abs(r) > 1e15) return { value: null, quality: 0 };
  return { value: r, quality };
}


// ===========================================================================
// Selection Tier B (6 blocks)
// ===========================================================================

function evalFirstGood(cfg: any, samples: InputSample[]): BlockResult {
  for (const s of samples) {
    if (s.quality >= GOOD_QUALITY && s.value !== null) {
      return { value: s.value, quality: GOOD_NON_SPECIFIC };
    }
  }
  return { value: null, quality: worstQuality(samples) };
}

function evalLastGood(cfg: any, samples: InputSample[]): BlockResult {
  for (let i = samples.length - 1; i >= 0; i--) {
    const s = samples[i];
    if (s.quality >= GOOD_QUALITY && s.value !== null) {
      return { value: s.value, quality: GOOD_NON_SPECIFIC };
    }
  }
  return { value: null, quality: worstQuality(samples) };
}

function evalHighestQuality(cfg: any, samples: InputSample[]): BlockResult {
  const valid = samples.filter(s => s.value !== null);
  if (!valid.length) return { value: null, quality: worstQuality(samples) };
  let best = valid[0];
  for (const s of valid) if (s.quality > best.quality) best = s;
  return { value: best.value, quality: best.quality };
}

function evalHotStandby(cfg: any, samples: InputSample[]): BlockResult {
  const [primary, standby] = samples;
  if (primary.quality >= GOOD_QUALITY && primary.value !== null) {
    return { value: primary.value, quality: GOOD_NON_SPECIFIC };
  }
  if (standby.quality >= GOOD_QUALITY && standby.value !== null) {
    return { value: standby.value, quality: GOOD_NON_SPECIFIC };
  }
  return { value: null, quality: Math.min(primary.quality, standby.quality) };
}

function evalVotingMofN(cfg: any, samples: InputSample[]): BlockResult {
  const tol = Number(cfg.tolerance);
  const n = samples.length;
  const m = cfg.min_agreement ?? Math.floor(n / 2) + 1;
  const good = samples
    .filter(s => s.quality >= GOOD_QUALITY && s.value !== null)
    .map(s => s.value as number)
    .sort((a, b) => a - b);
  if (good.length < m) return { value: null, quality: worstQuality(samples) };

  let bestCluster: number[] = [];
  for (let i = 0; i < good.length; i++) {
    let j = i;
    while (j < good.length && good[j] - good[i] <= tol) j++;
    const cluster = good.slice(i, j);
    if (cluster.length > bestCluster.length) bestCluster = cluster;
  }
  if (bestCluster.length < m) return { value: null, quality: worstQuality(samples) };
  return { value: median(bestCluster), quality: GOOD_NON_SPECIFIC };
}

function evalMuxIndex(cfg: any, samples: InputSample[]): BlockResult {
  const indexSample = samples[0];
  const valueSamples = samples.slice(1);
  if (indexSample.quality < GOOD_QUALITY || indexSample.value === null) {
    return { value: null, quality: indexSample.quality };
  }
  const idxFloat = indexSample.value;
  if (!Number.isInteger(idxFloat)) return { value: null, quality: 0 };
  const idx = idxFloat | 0;
  if (idx < 0 || idx >= valueSamples.length) return { value: null, quality: 0 };
  const sel = valueSamples[idx];
  return { value: sel.value, quality: sel.quality };
}


// ===========================================================================
// Conditional / Comparison / Logical Tier C (11 blocks)
// ===========================================================================

function evalIfThenElse(cfg: any, samples: InputSample[]): BlockResult {
  const [cond, t, e] = samples;
  if (cond.quality < GOOD_QUALITY || cond.value === null) {
    return { value: null, quality: cond.quality };
  }
  const chosen = cond.value > 0 ? t : e;
  if (chosen.value === null) return { value: null, quality: chosen.quality };
  return { value: chosen.value, quality: Math.min(chosen.quality, GOOD_NON_SPECIFIC) };
}

function compareCommon(
  cfg: any, samples: InputSample[], op: (l: number, r: number) => boolean,
): BlockResult {
  const ls = samples[0];
  if (ls.quality < GOOD_QUALITY || ls.value === null) {
    return { value: null, quality: ls.quality };
  }
  let rightVal: number;
  if (samples.length > 1) {
    const rs = samples[1];
    if (rs.quality < GOOD_QUALITY || rs.value === null) {
      return { value: null, quality: rs.quality };
    }
    rightVal = rs.value;
  } else {
    rightVal = Number(cfg.value);
  }
  return { value: op(ls.value, rightVal) ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}

function evalGT(cfg: any, samples: InputSample[]): BlockResult {
  return compareCommon(cfg, samples, (l, r) => l > r);
}
function evalLT(cfg: any, samples: InputSample[]): BlockResult {
  return compareCommon(cfg, samples, (l, r) => l < r);
}
function evalGTE(cfg: any, samples: InputSample[]): BlockResult {
  return compareCommon(cfg, samples, (l, r) => l >= r);
}
function evalLTE(cfg: any, samples: InputSample[]): BlockResult {
  return compareCommon(cfg, samples, (l, r) => l <= r);
}

function tolCompareCommon(
  cfg: any, samples: InputSample[], op: (l: number, r: number, tol: number) => boolean,
): BlockResult {
  const ls = samples[0];
  if (ls.quality < GOOD_QUALITY || ls.value === null) {
    return { value: null, quality: ls.quality };
  }
  let rightVal: number;
  if (samples.length > 1) {
    const rs = samples[1];
    if (rs.quality < GOOD_QUALITY || rs.value === null) {
      return { value: null, quality: rs.quality };
    }
    rightVal = rs.value;
  } else {
    rightVal = Number(cfg.value);
  }
  const tol = Number(cfg.tolerance ?? 0);
  return { value: op(ls.value, rightVal, tol) ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}

function evalEQ(cfg: any, samples: InputSample[]): BlockResult {
  return tolCompareCommon(cfg, samples, (l, r, tol) => Math.abs(l - r) <= tol);
}
function evalNE(cfg: any, samples: InputSample[]): BlockResult {
  return tolCompareCommon(cfg, samples, (l, r, tol) => Math.abs(l - r) > tol);
}

function evalAndOf(cfg: any, samples: InputSample[]): BlockResult {
  if (!allGood(samples)) return { value: null, quality: worstQuality(samples) };
  return { value: samples.every(s => (s.value as number) > 0) ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}

function evalOrOf(cfg: any, samples: InputSample[]): BlockResult {
  if (!allGood(samples)) return { value: null, quality: worstQuality(samples) };
  return { value: samples.some(s => (s.value as number) > 0) ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}

function evalXorOf(cfg: any, samples: InputSample[]): BlockResult {
  if (!allGood(samples)) return { value: null, quality: worstQuality(samples) };
  const trueCount = samples.filter(s => (s.value as number) > 0).length;
  return { value: trueCount % 2 === 1 ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}

function evalNot(cfg: any, samples: InputSample[]): BlockResult {
  const s = samples[0];
  if (s.quality < GOOD_QUALITY || s.value === null) {
    return { value: null, quality: s.quality };
  }
  return { value: s.value <= 0 ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}


// ===========================================================================
// SUM_OF (the original Tier 0 block, partial-sum semantics)
// ===========================================================================

function evalSumOf(cfg: any, samples: InputSample[]): BlockResult {
  if (!samples.length) return { value: null, quality: 0 };
  let total = 0;
  for (const s of samples) if (s.value !== null) total += s.value;
  const worst = worstQuality(samples);
  const q = worst >= GOOD_QUALITY ? GOOD_NON_SPECIFIC : worst;
  return { value: total, quality: q };
}


// ===========================================================================
// Input extraction (mirrors block.inputs() classmethod for each block)
// ===========================================================================

/** Extract tag IDs from a block config. Mirrors Python's inputs() classmethod.
 *  Used by the preview UI to know which sample values to source/prompt for. */
export function blockInputs(blockCode: string, cfg: any): number[] {
  if (!cfg) return [];

  // ADD / MUL: N-ary mode uses inputs:[{tag}|{value}], else binary {left, right?}
  if (blockCode === "ADD" || blockCode === "MUL") {
    if (isNaryMode(cfg)) {
      return (cfg.inputs as Array<{ tag?: number }>)
        .filter(i => "tag" in i && typeof i.tag === "number")
        .map(i => i.tag as number);
    }
    const ids = [Number(cfg.left)];
    if ("right" in cfg) ids.push(Number(cfg.right));
    return ids.filter(x => Number.isFinite(x));
  }

  // Other binary arithmetic & comparisons: {left, right?|value}
  if (BINARY_BLOCKS.has(blockCode)) {
    const ids = [Number(cfg.left)];
    if ("right" in cfg) ids.push(Number(cfg.right));
    return ids.filter(x => Number.isFinite(x));
  }

  // Unary math
  if (UNARY_BLOCKS.has(blockCode)) {
    return cfg.input != null ? [Number(cfg.input)] : [];
  }

  // Tag-list aggregations + selectors that take 'inputs'
  if (INPUTS_LIST_BLOCKS.has(blockCode)) {
    return Array.isArray(cfg.inputs) ? cfg.inputs.map((x: any) => Number(x)) : [];
  }

  // Special shapes
  if (blockCode === "HOT_STANDBY") {
    return [Number(cfg.primary), Number(cfg.standby)].filter(x => Number.isFinite(x));
  }
  if (blockCode === "MUX_INDEX") {
    const idx = Number(cfg.index);
    const vals = Array.isArray(cfg.values) ? cfg.values.map((x: any) => Number(x)) : [];
    return [idx, ...vals].filter(x => Number.isFinite(x));
  }
  if (blockCode === "IF_THEN_ELSE") {
    return [Number(cfg.condition), Number(cfg.then_value), Number(cfg.else_value)]
      .filter(x => Number.isFinite(x));
  }
  if (blockCode === "NOT") {
    return cfg.input != null ? [Number(cfg.input)] : [];
  }

  return [];
}


// ===========================================================================
// Block-code classification sets (used by blockInputs)
// ===========================================================================

const BINARY_BLOCKS = new Set([
  "SUB", "DIV", "MOD", "POW", "MIN_OF_TWO", "MAX_OF_TWO",
  "GT", "LT", "GTE", "LTE", "EQ", "NE",
]);

const UNARY_BLOCKS = new Set([
  "ABS", "NEG", "SQRT", "FLOOR", "CEIL", "ROUND",
  "EXP", "LN", "LOG10", "SIN", "COS", "TAN",
]);

const INPUTS_LIST_BLOCKS = new Set([
  "SUM_OF",
  "AVG_OF", "MIN_OF", "MAX_OF", "MEDIAN_OF", "MODE_OF", "RANGE_OF",
  "STDDEV_OF", "VARIANCE_OF", "RMS_OF",
  "PRODUCT_OF", "GEOMETRIC_MEAN", "HARMONIC_MEAN", "WEIGHTED_AVG",
  "COUNT_GOOD", "COUNT_NONZERO",
  "FIRST_GOOD", "LAST_GOOD", "HIGHEST_QUALITY", "VOTING_M_OF_N",
  "AND_OF", "OR_OF", "XOR_OF",
]);


// ===========================================================================
// Registry + public dispatch
// ===========================================================================

type Evaluator = (cfg: any, samples: InputSample[]) => BlockResult;

const STATELESS_EVALUATORS: Record<string, Evaluator> = {
  // Aggregation
  AVG_OF: evalAvgOf, MIN_OF: evalMinOf, MAX_OF: evalMaxOf,
  MEDIAN_OF: evalMedianOf, MODE_OF: evalModeOf, RANGE_OF: evalRangeOf,
  STDDEV_OF: evalStddevOf, VARIANCE_OF: evalVarianceOf, RMS_OF: evalRmsOf,
  PRODUCT_OF: evalProductOf, GEOMETRIC_MEAN: evalGeometricMean,
  HARMONIC_MEAN: evalHarmonicMean, WEIGHTED_AVG: evalWeightedAvg,
  COUNT_GOOD: evalCountGood, COUNT_NONZERO: evalCountNonzero,
  // Arithmetic
  ADD: evalAdd, SUB: evalSub, MUL: evalMul, DIV: evalDiv, MOD: evalMod,
  POW: evalPow, MIN_OF_TWO: evalMinOfTwo, MAX_OF_TWO: evalMaxOfTwo,
  ABS: evalAbs, NEG: evalNeg, SQRT: evalSqrt,
  FLOOR: evalFloor, CEIL: evalCeil, ROUND: evalRound,
  EXP: evalExp, LN: evalLn, LOG10: evalLog10,
  SIN: evalSin, COS: evalCos, TAN: evalTan,
  // Selection
  FIRST_GOOD: evalFirstGood, LAST_GOOD: evalLastGood,
  HIGHEST_QUALITY: evalHighestQuality, HOT_STANDBY: evalHotStandby,
  VOTING_M_OF_N: evalVotingMofN, MUX_INDEX: evalMuxIndex,
  // Conditional / comparison / logic
  IF_THEN_ELSE: evalIfThenElse,
  GT: evalGT, LT: evalLT, GTE: evalGTE, LTE: evalLTE, EQ: evalEQ, NE: evalNE,
  AND_OF: evalAndOf, OR_OF: evalOrOf, XOR_OF: evalXorOf, NOT: evalNot,
  // SUM_OF
  SUM_OF: evalSumOf,
};


/**
 * Run a block evaluator with the supplied config and inputs.
 *
 * Returns:
 *   - {status: 'ok', value, quality} for successful stateless eval
 *   - {status: 'stateful_deferred', ...} for stateful blocks - caller must
 *     hit /api/computed-tags/preview instead
 *   - {status: 'unknown_block', ...} if no evaluator exists
 *   - {status: 'execution_error', error, ...} if the evaluator threw
 */
export function evaluateBlockJS(
  blockCode: string,
  config: any,
  samples: InputSample[],
): PreviewResult {
  if (isStateful(blockCode)) {
    return {
      value: null, quality: 0,
      status: "stateful_deferred",
      error: "Stateful block - use backend /api/computed-tags/preview",
    };
  }
  const evaluator = STATELESS_EVALUATORS[blockCode];
  if (!evaluator) {
    return {
      value: null, quality: 0,
      status: "unknown_block",
      error: `No JS evaluator for block code '${blockCode}'`,
    };
  }
  try {
    const r = evaluator(config, samples);
    return { ...r, status: "ok" };
  } catch (e) {
    return {
      value: null, quality: 0,
      status: "execution_error",
      error: e instanceof Error ? e.message : String(e),
    };
  }
}


/** Build a default sample for a tag input - value=1, quality=GOOD.
 *  Matches the backend preview's default behavior. Lets the UI render
 *  a useful result before the user supplies sample values. */
export function defaultSample(tagId: number): InputSample {
  return { tag_id: tagId, value: 1, quality: GOOD_NON_SPECIFIC };
}


/** Build samples for all inputs the block needs, applying user overrides. */
export function buildSamples(
  blockCode: string,
  config: any,
  overrides: Map<number, { value: number | null; quality: number }>,
): InputSample[] {
  const tagIds = blockInputs(blockCode, config);
  return tagIds.map(tid => {
    const ov = overrides.get(tid);
    if (ov) return { tag_id: tid, value: ov.value, quality: ov.quality };
    return defaultSample(tid);
  });
}
