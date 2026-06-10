/**
 * Block icons.
 *
 * Maps a calc block CODE (block_type) to a lucide-react icon, with a
 * category fallback for codes added later. Covers every code currently
 * in BLOCK_REGISTRY across the five tiers:
 *
 *   Tier A  aggregation         AVG_OF, MIN_OF, MAX_OF, SUM_OF, ...
 *   Tier B  selection/redundancy FIRST_GOOD, VOTING_M_OF_N, HOT_STANDBY, ...
 *   Tier C  conditional/logic    AND_OF, EQ, GT, IF_THEN_ELSE, ...
 *   Tier D  stateful             TON, TOF, CTU, R_TRIG, RS, ...
 *   Tier E  arithmetic/trig      ADD, DIV, POW, SQRT, SIN, ...
 *
 * Usage:
 *   import { BlockIcon } from "@/lib/blockIcons";
 *   <BlockIcon code={def.block_type} category={type?.category} className="h-4 w-4" />
 */
import {
  Plus, Minus, X, Divide, Percent, FunctionSquare, Calculator, Ruler,
  Waves, ArrowUpDown, Sigma, Activity, Hash, ListFilter, Shuffle,
  ShieldCheck, Binary, Equal, GitCompare, Split, Timer, ToggleLeft,
  ToggleRight, Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

const BY_CODE: Record<string, LucideIcon> = {
  // ── Tier E: arithmetic / transcendental ──────────────────────────
  ADD: Plus, SUB: Minus, NEG: Minus, MUL: X, DIV: Divide, MOD: Percent,
  POW: FunctionSquare, EXP: FunctionSquare, LN: FunctionSquare, LOG10: FunctionSquare,
  SQRT: FunctionSquare, ABS: Calculator,
  FLOOR: Ruler, CEIL: Ruler, ROUND: Ruler,
  SIN: Waves, COS: Waves, TAN: Waves,
  MIN_OF_TWO: ArrowUpDown, MAX_OF_TWO: ArrowUpDown,

  // ── Tier A: aggregation ──────────────────────────────────────────
  AVG_OF: Sigma, SUM_OF: Sigma, WEIGHTED_AVG: Sigma,
  GEOMETRIC_MEAN: Sigma, HARMONIC_MEAN: Sigma, MEDIAN_OF: Sigma, MODE_OF: Sigma,
  PRODUCT_OF: X,
  MIN_OF: ArrowUpDown, MAX_OF: ArrowUpDown, RANGE_OF: ArrowUpDown,
  STDDEV_OF: Activity, VARIANCE_OF: Activity, RMS_OF: Activity,
  COUNT_GOOD: Hash, COUNT_NONZERO: Hash,

  // ── Tier B: selection / redundancy ───────────────────────────────
  FIRST_GOOD: ListFilter, LAST_GOOD: ListFilter, HIGHEST_QUALITY: ListFilter,
  MUX_INDEX: Shuffle,
  VOTING_M_OF_N: ShieldCheck, HOT_STANDBY: ShieldCheck,

  // ── Tier C: conditional / logic ──────────────────────────────────
  AND_OF: Binary, OR_OF: Binary, NOT: Binary,
  EQ: Equal,
  NE: GitCompare, GT: GitCompare, GTE: GitCompare, LT: GitCompare, LTE: GitCompare,
  IF_THEN_ELSE: Split,

  // ── Tier D: stateful (timers / counters / latches / edges) ───────
  TON: Timer, TOF: Timer, TP: Timer,
  CTU: Hash, CTD: Hash,
  RS: ToggleLeft, SR: ToggleRight,
  R_TRIG: Zap, F_TRIG: Zap,
};

const DEFAULT_ICON: LucideIcon = FunctionSquare;

/** Pick an icon by code first, then by category, then a default. */
export function blockIconFor(code: string | null | undefined, category?: string | null): LucideIcon {
  if (code && BY_CODE[code]) return BY_CODE[code];
  const c = (category ?? "").toLowerCase();
  if (c.includes("agg")) return Sigma;
  if (c.includes("arith") || c.includes("math")) return Calculator;
  if (c.includes("select") || c.includes("redund")) return ListFilter;
  if (c.includes("logic") || c.includes("cond") || c.includes("compar")) return Binary;
  if (c.includes("state") || c.includes("timer") || c.includes("count")) return Timer;
  return DEFAULT_ICON;
}

export function BlockIcon(props: { code: string | null | undefined; category?: string | null; className?: string }) {
  const Icon = blockIconFor(props.code, props.category);
  return <Icon className={props.className ?? "h-4 w-4"} aria-hidden />;
}
