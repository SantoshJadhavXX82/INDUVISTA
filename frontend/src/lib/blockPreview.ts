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

/** Aggregation output quality. Computes from samples (tag operands)
 *  — constants don't drag quality down (always inline-GOOD). Returns
 *  GOOD_NON_SPECIFIC if all tag samples are GOOD; else the worst quality. */
function aggOutputQuality(_cfg: any, samples: InputSample[]): number {
  if (!samples || samples.length === 0) {
    // Pure-constant list — no tag samples to drag quality. Still need
    // at least one operand in cfg to be a valid block; assume so.
    return GOOD_NON_SPECIFIC;
  }
  const w = worstQuality(samples);
  return w >= GOOD_QUALITY ? GOOD_NON_SPECIFIC : w;
}

/** Extract numeric values from cfg.inputs operand specs + matching
 *  tag samples. Tag operands with null values are dropped (matches
 *  Python _good_values semantics). Constants always contribute. */
function goodValues(cfg: any, samples: InputSample[]): number[] {
  const inputs: any[] = (cfg && cfg.inputs) || [];
  const out: number[] = [];
  let si = 0;
  for (const spec of inputs) {
    const op = resolveOperandSpec(spec);
    if (op.tag !== null) {
      const s = samples[si++];
      if (s && s.value !== null) out.push(s.value);
    } else if (op.const !== null) {
      out.push(op.const);
    }
  }
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


// ---------------------------------------------------------------------------
// Universal operand-spec resolver (Phase 17.0c)
// Mirrors backend/app/workers/calc_blocks/base.py:resolve_operand_spec.
// Accepts three shapes:
//   bare positive int       → {tag: id}        (legacy)
//   {tag: id}               → {tag: id}        (new)
//   {value: number}         → {value: number}  (new)
// ---------------------------------------------------------------------------

interface ResolvedOp { tag: number | null; const: number | null; }

function resolveOperandSpec(spec: any): ResolvedOp {
  if (typeof spec === "number" && Number.isInteger(spec) && spec > 0) {
    return { tag: spec, const: null };
  }
  if (spec && typeof spec === "object" && !Array.isArray(spec)) {
    if ("tag" in spec) {
      const t = Number((spec as any).tag);
      if (Number.isInteger(t) && t > 0) return { tag: t, const: null };
    }
    if ("value" in spec) {
      const v = Number((spec as any).value);
      if (Number.isFinite(v)) return { tag: null, const: v };
    }
  }
  // Invalid / unresolved → treat as tag=null (BAD) so caller emits BAD.
  return { tag: null, const: null };
}

/** Returns operand tag id if spec is a tag, else null (constant or invalid). */
function operandTagId(spec: any): number | null {
  return resolveOperandSpec(spec).tag;
}

/** Collect tag ids from a list of operand specs, skipping constants. */
function collectListTagIds(specs: any[]): number[] {
  const out: number[] = [];
  for (const s of specs ?? []) {
    const t = operandTagId(s);
    if (t !== null) out.push(t);
  }
  return out;
}


/** Resolve binary block operands. Each of left/right may be a tag
 *  reference or a numeric constant. Returns null on BAD input. */
function binaryOperands(
  cfg: any, samples: InputSample[],
): { left: number; right: number; quality: number } | { left: null; right: null; quality: number } {
  let sampleIdx = 0;
  let worstQ = GOOD_NON_SPECIFIC;

  // left
  const L = resolveOperandSpec(cfg.left);
  let leftVal: number;
  if (L.tag !== null) {
    const ls = samples[sampleIdx++];
    if (!ls || ls.quality < GOOD_QUALITY || ls.value === null) {
      return { left: null, right: null, quality: ls?.quality ?? 0 };
    }
    leftVal = ls.value;
    worstQ = Math.min(worstQ, ls.quality);
  } else if (L.const !== null) {
    leftVal = L.const;
  } else {
    return { left: null, right: null, quality: 0 };
  }

  // right — accepts new shape OR legacy global `value` key
  let rightVal: number;
  if ("right" in cfg) {
    const R = resolveOperandSpec(cfg.right);
    if (R.tag !== null) {
      const rs = samples[sampleIdx++];
      if (!rs || rs.quality < GOOD_QUALITY || rs.value === null) {
        return { left: null, right: null, quality: rs?.quality ?? 0 };
      }
      rightVal = rs.value;
      worstQ = Math.min(worstQ, rs.quality);
    } else if (R.const !== null) {
      rightVal = R.const;
    } else {
      return { left: null, right: null, quality: 0 };
    }
  } else if ("value" in cfg) {
    // Legacy: top-level `value` means right-as-constant
    rightVal = Number(cfg.value);
    if (!Number.isFinite(rightVal)) {
      return { left: null, right: null, quality: 0 };
    }
  } else {
    return { left: null, right: null, quality: 0 };
  }

  return { left: leftVal, right: rightVal, quality: worstQ };
}

function unaryOperand(
  cfg: any, samples: InputSample[],
): { v: number; quality: number } | { v: null; quality: number } {
  const op = resolveOperandSpec(cfg.input);
  if (op.tag !== null) {
    const s = samples[0];
    if (!s || s.quality < GOOD_QUALITY || s.value === null) {
      return { v: null, quality: s?.quality ?? 0 };
    }
    return { v: s.value, quality: GOOD_NON_SPECIFIC };
  }
  if (op.const !== null) return { v: op.const, quality: GOOD_NON_SPECIFIC };
  return { v: null, quality: 0 };
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
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  return { value: vals.reduce((a, b) => a + b, 0) / vals.length, quality: aggOutputQuality(cfg, samples) };
}

function evalMinOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  return { value: Math.min(...vals), quality: aggOutputQuality(cfg, samples) };
}

function evalMaxOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  return { value: Math.max(...vals), quality: aggOutputQuality(cfg, samples) };
}

function evalMedianOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  return { value: median(vals), quality: aggOutputQuality(cfg, samples) };
}

function evalModeOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  return { value: mode(vals), quality: aggOutputQuality(cfg, samples) };
}

function evalRangeOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  return { value: Math.max(...vals) - Math.min(...vals), quality: aggOutputQuality(cfg, samples) };
}

function evalStddevOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (vals.length < 2) return { value: null, quality: aggOutputQuality(cfg, samples) };
  return { value: stdev(vals), quality: aggOutputQuality(cfg, samples) };
}

function evalVarianceOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (vals.length < 2) return { value: null, quality: aggOutputQuality(cfg, samples) };
  return { value: variance(vals), quality: aggOutputQuality(cfg, samples) };
}

function evalRmsOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  const meanSq = vals.reduce((a, b) => a + b * b, 0) / vals.length;
  return { value: Math.sqrt(meanSq), quality: aggOutputQuality(cfg, samples) };
}

function evalProductOf(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  return { value: vals.reduce((a, b) => a * b, 1), quality: aggOutputQuality(cfg, samples) };
}

function evalGeometricMean(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  if (vals.some(v => v <= 0)) return { value: null, quality: 0 };
  const sumLog = vals.reduce((a, b) => a + Math.log(b), 0);
  return { value: Math.exp(sumLog / vals.length), quality: aggOutputQuality(cfg, samples) };
}

function evalHarmonicMean(cfg: any, samples: InputSample[]): BlockResult {
  const vals = goodValues(cfg, samples);
  if (!vals.length) return { value: null, quality: aggOutputQuality(cfg, samples) };
  if (vals.some(v => v === 0)) return { value: null, quality: 0 };
  return { value: vals.length / vals.reduce((a, b) => a + 1 / b, 0), quality: aggOutputQuality(cfg, samples) };
}

function evalWeightedAvg(cfg: any, samples: InputSample[]): BlockResult {
  const inputSpecs: any[] = cfg.inputs ?? [];
  const weightSpecs: any[] = cfg.weights ?? [];
  // Worker delivers samples in order: all input tags, then all weight tags
  const nInputTags = collectListTagIds(inputSpecs).length;
  const inputSamples = samples.slice(0, nInputTags);
  const weightSamples = samples.slice(nInputTags);
  let inIdx = 0, wIdx = 0;
  let ws = 0, wt = 0;
  let worstQ = GOOD_NON_SPECIFIC;
  for (let i = 0; i < inputSpecs.length; i++) {
    const vSpec = inputSpecs[i];
    const wSpec = weightSpecs[i];
    if (!wSpec) continue;
    // resolve value
    const vop = resolveOperandSpec(vSpec);
    let value: number | null = null;
    if (vop.tag !== null) {
      const s = inputSamples[inIdx++];
      if (s) {
        worstQ = Math.min(worstQ, s.quality);
        if (s.value !== null) value = s.value;
      }
    } else if (vop.const !== null) {
      value = vop.const;
    }
    if (value === null) continue;
    // resolve weight
    const wop = resolveOperandSpec(wSpec);
    let weight: number | null = null;
    if (wop.tag !== null) {
      const s = weightSamples[wIdx++];
      if (s) {
        worstQ = Math.min(worstQ, s.quality);
        if (s.value !== null && s.value > 0) weight = s.value;
      }
    } else if (wop.const !== null && wop.const > 0) {
      weight = wop.const;
    }
    if (weight === null) continue;
    ws += value * weight;
    wt += weight;
  }
  const outQ = worstQ >= GOOD_QUALITY ? GOOD_NON_SPECIFIC : worstQ;
  if (wt <= 0) return { value: null, quality: outQ };
  return { value: ws / wt, quality: outQ };
}

function evalCountGood(cfg: any, samples: InputSample[]): BlockResult {
  const tagGood = samples.filter(s => s.quality >= GOOD_QUALITY).length;
  // Constants are always inline-GOOD; count them too
  const constCount = (cfg.inputs as any[] ?? []).filter(
    s => resolveOperandSpec(s).tag === null
  ).length;
  return { value: tagGood + constCount, quality: GOOD_NON_SPECIFIC };
}

function evalCountNonzero(cfg: any, samples: InputSample[]): BlockResult {
  const tagNz = samples.filter(s => s.value !== null && s.value !== 0).length;
  const constNz = (cfg.inputs as any[] ?? []).filter(s => {
    const op = resolveOperandSpec(s);
    return op.tag === null && op.const !== null && op.const !== 0;
  }).length;
  return { value: tagNz + constNz, quality: GOOD_NON_SPECIFIC };
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
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  return { value: Math.abs(v), quality };
}

function evalNeg(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  return { value: -v, quality };
}

function evalSqrt(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  if (v < 0) return { value: null, quality: 0 };
  return { value: Math.sqrt(v), quality };
}

function evalFloor(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  return { value: Math.floor(v), quality };
}

function evalCeil(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  return { value: Math.ceil(v), quality };
}

function evalRound(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  return { value: bankerRound(v), quality };
}

function evalExp(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  const r = Math.exp(v);
  if (!Number.isFinite(r)) return { value: null, quality: 0 };
  return { value: r, quality };
}

function evalLn(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  if (v <= 0) return { value: null, quality: 0 };
  return { value: Math.log(v), quality };
}

function evalLog10(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  if (v <= 0) return { value: null, quality: 0 };
  return { value: Math.log10(v), quality };
}

function evalSin(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  return { value: Math.sin(v), quality };
}

function evalCos(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  return { value: Math.cos(v), quality };
}

function evalTan(cfg: any, samples: InputSample[]): BlockResult {
  const { v, quality } = unaryOperand(cfg, samples);
  if (v === null) return { value: null, quality };
  const r = Math.tan(v);
  if (!Number.isFinite(r) || Math.abs(r) > 1e15) return { value: null, quality: 0 };
  return { value: r, quality };
}


// ===========================================================================
// Selection Tier B (6 blocks)
// ===========================================================================

function evalFirstGood(cfg: any, samples: InputSample[]): BlockResult {
  // Walk specs in declared order, constants count as GOOD
  let si = 0;
  for (const spec of (cfg.inputs as any[] ?? [])) {
    const op = resolveOperandSpec(spec);
    if (op.tag !== null) {
      const s = samples[si++];
      if (s && s.quality >= GOOD_QUALITY && s.value !== null) {
        return { value: s.value, quality: GOOD_NON_SPECIFIC };
      }
    } else if (op.const !== null) {
      return { value: op.const, quality: GOOD_NON_SPECIFIC };
    }
  }
  return { value: null, quality: worstQuality(samples) };
}

function evalLastGood(cfg: any, samples: InputSample[]): BlockResult {
  // Build a per-spec sample mapping (constants get null), walk reversed.
  const specs: any[] = cfg.inputs ?? [];
  const perSpec: Array<{spec: any; sample: InputSample | null}> = [];
  let si = 0;
  for (const spec of specs) {
    if (resolveOperandSpec(spec).tag !== null) {
      perSpec.push({spec, sample: samples[si++] ?? null});
    } else {
      perSpec.push({spec, sample: null});
    }
  }
  for (let i = perSpec.length - 1; i >= 0; i--) {
    const {spec, sample} = perSpec[i];
    const op = resolveOperandSpec(spec);
    if (op.tag !== null) {
      if (sample && sample.quality >= GOOD_QUALITY && sample.value !== null) {
        return { value: sample.value, quality: GOOD_NON_SPECIFIC };
      }
    } else if (op.const !== null) {
      return { value: op.const, quality: GOOD_NON_SPECIFIC };
    }
  }
  return { value: null, quality: worstQuality(samples) };
}

function evalHighestQuality(cfg: any, samples: InputSample[]): BlockResult {
  // Build candidates: (value, quality) tuples — constants get GOOD_NON_SPECIFIC.
  type Cand = { value: number; quality: number };
  const cands: Cand[] = [];
  let si = 0;
  for (const spec of (cfg.inputs as any[] ?? [])) {
    const op = resolveOperandSpec(spec);
    if (op.tag !== null) {
      const s = samples[si++];
      if (s && s.value !== null) cands.push({ value: s.value, quality: s.quality });
    } else if (op.const !== null) {
      cands.push({ value: op.const, quality: GOOD_NON_SPECIFIC });
    }
  }
  if (!cands.length) return { value: null, quality: worstQuality(samples) };
  let best = cands[0];
  for (const c of cands) if (c.quality > best.quality) best = c;
  return { value: best.value, quality: best.quality };
}

function evalHotStandby(cfg: any, samples: InputSample[]): BlockResult {
  let si = 0;
  function resolve(key: string): { value: number | null; quality: number } {
    const op = resolveOperandSpec(cfg[key]);
    if (op.tag !== null) {
      const s = samples[si++];
      return { value: s?.value ?? null, quality: s?.quality ?? 0 };
    }
    return { value: op.const, quality: GOOD_NON_SPECIFIC };
  }
  const p = resolve("primary");
  const sd = resolve("standby");
  if (p.quality >= GOOD_QUALITY && p.value !== null) {
    return { value: p.value, quality: GOOD_NON_SPECIFIC };
  }
  if (sd.quality >= GOOD_QUALITY && sd.value !== null) {
    return { value: sd.value, quality: GOOD_NON_SPECIFIC };
  }
  return { value: null, quality: Math.min(p.quality, sd.quality) };
}

function evalVotingMofN(cfg: any, samples: InputSample[]): BlockResult {
  const tol = Number(cfg.tolerance);
  const specs: any[] = cfg.inputs ?? [];
  const n = specs.length;
  const m = cfg.min_agreement ?? Math.floor(n / 2) + 1;
  // Collect GOOD numeric values from tag samples + every constant
  const goodVals: number[] = [];
  let si = 0;
  for (const spec of specs) {
    const op = resolveOperandSpec(spec);
    if (op.tag !== null) {
      const s = samples[si++];
      if (s && s.quality >= GOOD_QUALITY && s.value !== null) {
        goodVals.push(s.value);
      }
    } else if (op.const !== null) {
      goodVals.push(op.const);
    }
  }
  const good = goodVals.sort((a, b) => a - b);
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
  let si = 0;
  // Resolve index
  const idxOp = resolveOperandSpec(cfg.index);
  let idxFloat: number;
  if (idxOp.tag !== null) {
    const indexSample = samples[si++];
    if (!indexSample || indexSample.quality < GOOD_QUALITY || indexSample.value === null) {
      return { value: null, quality: indexSample?.quality ?? 0 };
    }
    idxFloat = indexSample.value;
  } else if (idxOp.const !== null) {
    idxFloat = idxOp.const;
  } else {
    return { value: null, quality: 0 };
  }
  if (!Number.isInteger(idxFloat)) return { value: null, quality: 0 };
  const idx = idxFloat | 0;
  const valuesSpecs: any[] = cfg.values ?? [];
  if (idx < 0 || idx >= valuesSpecs.length) return { value: null, quality: 0 };
  // Find the sample for the selected value by walking prior specs
  for (const priorSpec of valuesSpecs.slice(0, idx)) {
    if (resolveOperandSpec(priorSpec).tag !== null) si++;
  }
  const selOp = resolveOperandSpec(valuesSpecs[idx]);
  if (selOp.tag !== null) {
    const sel = samples[si];
    return { value: sel?.value ?? null, quality: sel?.quality ?? 0 };
  }
  return { value: selOp.const, quality: GOOD_NON_SPECIFIC };
}


// ===========================================================================
// Conditional / Comparison / Logical Tier C (11 blocks)
// ===========================================================================

function evalIfThenElse(cfg: any, samples: InputSample[]): BlockResult {
  let si = 0;
  function resolve(key: string): { value: number | null; quality: number } {
    const op = resolveOperandSpec(cfg[key]);
    if (op.tag !== null) {
      const s = samples[si++];
      return { value: s?.value ?? null, quality: s?.quality ?? 0 };
    }
    return { value: op.const, quality: GOOD_NON_SPECIFIC };
  }
  const cond = resolve("condition");
  if (cond.quality < GOOD_QUALITY || cond.value === null) {
    return { value: null, quality: cond.quality };
  }
  // Resolve BOTH branches so si advances in declaration order
  const t = resolve("then_value");
  const e = resolve("else_value");
  const chosen = cond.value > 0 ? t : e;
  if (chosen.value === null) return { value: null, quality: chosen.quality };
  return { value: chosen.value, quality: Math.min(chosen.quality, GOOD_NON_SPECIFIC) };
}

function compareCommon(
  cfg: any, samples: InputSample[], op: (l: number, r: number) => boolean,
): BlockResult {
  const r = binaryOperands(cfg, samples);
  if (r.left === null) return { value: null, quality: r.quality };
  return { value: op(r.left, r.right) ? 1 : 0, quality: GOOD_NON_SPECIFIC };
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
  const r = binaryOperands(cfg, samples);
  if (r.left === null) return { value: null, quality: r.quality };
  const tol = Number(cfg.tolerance ?? 0);
  return { value: op(r.left, r.right, tol) ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}

function evalEQ(cfg: any, samples: InputSample[]): BlockResult {
  return tolCompareCommon(cfg, samples, (l, r, tol) => Math.abs(l - r) <= tol);
}
function evalNE(cfg: any, samples: InputSample[]): BlockResult {
  return tolCompareCommon(cfg, samples, (l, r, tol) => Math.abs(l - r) > tol);
}

function evalAndOf(cfg: any, samples: InputSample[]): BlockResult {
  // Each operand: tag (must be GOOD) or constant (value > 0).
  const bools: boolean[] = [];
  let si = 0;
  for (const spec of (cfg.inputs as any[] ?? [])) {
    const op = resolveOperandSpec(spec);
    if (op.tag !== null) {
      const s = samples[si++];
      if (!s || s.quality < GOOD_QUALITY || s.value === null) {
        return { value: null, quality: s?.quality ?? 0 };
      }
      bools.push(s.value > 0);
    } else if (op.const !== null) {
      bools.push(op.const > 0);
    } else {
      return { value: null, quality: 0 };
    }
  }
  return { value: bools.every(b => b) ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}

function evalOrOf(cfg: any, samples: InputSample[]): BlockResult {
  const bools: boolean[] = [];
  let si = 0;
  for (const spec of (cfg.inputs as any[] ?? [])) {
    const op = resolveOperandSpec(spec);
    if (op.tag !== null) {
      const s = samples[si++];
      if (!s || s.quality < GOOD_QUALITY || s.value === null) {
        return { value: null, quality: s?.quality ?? 0 };
      }
      bools.push(s.value > 0);
    } else if (op.const !== null) {
      bools.push(op.const > 0);
    } else {
      return { value: null, quality: 0 };
    }
  }
  return { value: bools.some(b => b) ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}

function evalXorOf(cfg: any, samples: InputSample[]): BlockResult {
  const bools: boolean[] = [];
  let si = 0;
  for (const spec of (cfg.inputs as any[] ?? [])) {
    const op = resolveOperandSpec(spec);
    if (op.tag !== null) {
      const s = samples[si++];
      if (!s || s.quality < GOOD_QUALITY || s.value === null) {
        return { value: null, quality: s?.quality ?? 0 };
      }
      bools.push(s.value > 0);
    } else if (op.const !== null) {
      bools.push(op.const > 0);
    } else {
      return { value: null, quality: 0 };
    }
  }
  const trueCount = bools.filter(b => b).length;
  return { value: trueCount % 2 === 1 ? 1 : 0, quality: GOOD_NON_SPECIFIC };
}

function evalNot(cfg: any, samples: InputSample[]): BlockResult {
  const op = resolveOperandSpec(cfg.input);
  let val: number;
  if (op.tag !== null) {
    const s = samples[0];
    if (!s || s.quality < GOOD_QUALITY || s.value === null) {
      return { value: null, quality: s?.quality ?? 0 };
    }
    val = s.value;
  } else if (op.const !== null) {
    val = op.const;
  } else {
    return { value: null, quality: 0 };
  }
  return { value: val <= 0 ? 1 : 0, quality: GOOD_NON_SPECIFIC };
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
      return collectListTagIds(cfg.inputs ?? []);
    }
    // Binary mode — use universal resolver on left + right
    const ids: number[] = [];
    const L = operandTagId(cfg.left);
    if (L !== null) ids.push(L);
    if ("right" in cfg) {
      const R = operandTagId(cfg.right);
      if (R !== null) ids.push(R);
    }
    // legacy top-level `value` never contributes a tag
    return ids;
  }

  // Other binary arithmetic & comparisons: {left, right?|value} where
  // each of left/right may be a tag or a constant operand spec.
  if (BINARY_BLOCKS.has(blockCode)) {
    const ids: number[] = [];
    const L = operandTagId(cfg.left);
    if (L !== null) ids.push(L);
    if ("right" in cfg) {
      const R = operandTagId(cfg.right);
      if (R !== null) ids.push(R);
    }
    return ids;
  }

  // Unary math + transcendental + NOT — input may be tag or constant
  if (UNARY_BLOCKS.has(blockCode)) {
    const t = operandTagId(cfg.input);
    return t !== null ? [t] : [];
  }

  // Tag-list aggregations + selectors that take 'inputs'
  if (INPUTS_LIST_BLOCKS.has(blockCode)) {
    return collectListTagIds(cfg.inputs ?? []);
  }

  // Special shapes
  if (blockCode === "HOT_STANDBY") {
    const ids: number[] = [];
    const p = operandTagId(cfg.primary);
    const s = operandTagId(cfg.standby);
    if (p !== null) ids.push(p);
    if (s !== null) ids.push(s);
    return ids;
  }
  if (blockCode === "MUX_INDEX") {
    const ids: number[] = [];
    const i = operandTagId(cfg.index);
    if (i !== null) ids.push(i);
    ids.push(...collectListTagIds(cfg.values ?? []));
    return ids;
  }
  if (blockCode === "IF_THEN_ELSE") {
    const ids: number[] = [];
    for (const key of ["condition", "then_value", "else_value"]) {
      const t = operandTagId(cfg[key]);
      if (t !== null) ids.push(t);
    }
    return ids;
  }
  if (blockCode === "WEIGHTED_AVG") {
    return [
      ...collectListTagIds(cfg.inputs ?? []),
      ...collectListTagIds(cfg.weights ?? []),
    ];
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
/** Default sample for a tag, used when the preview has no user override.
 *  If a live value is available, use it (most useful default — preview
 *  reflects what the block would actually compute right now). Otherwise
 *  fall back to value=1 — innocuous placeholder so binary/aggregation
 *  blocks still produce a number rather than BAD. */
export function defaultSample(
  tagId: number,
  liveValues?: Map<number, { value: number | null; quality: number }>,
): InputSample {
  const live = liveValues?.get(tagId);
  if (live && live.value !== null && live.quality >= GOOD_QUALITY) {
    return { tag_id: tagId, value: live.value, quality: live.quality };
  }
  return { tag_id: tagId, value: 1, quality: GOOD_NON_SPECIFIC };
}


/** Build samples for all inputs the block needs, applying user overrides. */
export function buildSamples(
  blockCode: string,
  config: any,
  overrides: Map<number, { value: number | null; quality: number }>,
  liveValues?: Map<number, { value: number | null; quality: number }>,
): InputSample[] {
  const tagIds = blockInputs(blockCode, config);
  return tagIds.map(tid => {
    const ov = overrides.get(tid);
    if (ov) return { tag_id: tid, value: ov.value, quality: ov.quality };
    return defaultSample(tid, liveValues);
  });
}
