/**
 * Block example equations (reference forms).
 *
 * Maps each calc block CODE to a short equation example written in terms
 * of its real block_config field names, NOT specific tag ids. Use it as
 * reference next to a calc's actual config.
 *
 * Conventions used in the expressions:
 *   - "out" is the computed tag (callers usually prefix the tag name).
 *   - Binary blocks: `left` is a tag; the right operand is either a tag
 *     (`right`) or a numeric constant (`value`) - shown here as `right`.
 *   - `inputs[]` is the ordered tag list (tag_ref_list).
 *   - Logical/boolean inputs treat value > 0 as TRUE; comparison and
 *     logic blocks output 1 (TRUE) or 0 (FALSE).
 *
 * Verified against backend/app/workers/calc_blocks/* (semantics) and
 * calc_block_schemas.py (field names).
 */

export const BLOCK_EXAMPLES: Record<string, string> = {
  // ── Arithmetic — binary (left, right|value) ──────────────────────
  ADD:        "left + right        (or inputs[0] + … + inputs[n] in N-ary mode)",
  SUB:        "left - right",
  MUL:        "left * right        (or inputs[0] * … * inputs[n] in N-ary mode)",
  DIV:        "left / right        (BAD if right = 0)",
  MOD:        "left mod right",
  POW:        "left ^ right",
  MIN_OF_TWO: "min(left, right)",
  MAX_OF_TWO: "max(left, right)",

  // ── Math — unary (input) ─────────────────────────────────────────
  ABS:   "abs(input)",
  NEG:   "-input",
  SQRT:  "sqrt(input)             (BAD if input < 0)",
  FLOOR: "floor(input)",
  CEIL:  "ceil(input)",
  ROUND: "round(input)",
  EXP:   "e ^ input",
  LN:    "ln(input)               (BAD if input <= 0)",
  LOG10: "log10(input)            (BAD if input <= 0)",
  SIN:   "sin(input)              (radians)",
  COS:   "cos(input)              (radians)",
  TAN:   "tan(input)              (radians)",

  // ── Aggregation (inputs[]) ───────────────────────────────────────
  SUM_OF:         "inputs[0] + inputs[1] + … + inputs[n]",
  AVG_OF:         "mean(inputs) = sum(inputs) / n",
  MIN_OF:         "min(inputs)",
  MAX_OF:         "max(inputs)",
  MEDIAN_OF:      "median(inputs)",
  MODE_OF:        "mode(inputs)",
  RANGE_OF:       "max(inputs) - min(inputs)",
  RMS_OF:         "sqrt(mean(inputs^2))",
  PRODUCT_OF:     "inputs[0] * inputs[1] * … * inputs[n]",
  GEOMETRIC_MEAN: "(inputs[0] * … * inputs[n]) ^ (1/n)   (inputs > 0)",
  HARMONIC_MEAN:  "n / sum(1/inputs)                     (inputs != 0)",
  STDDEV_OF:      "sqrt( sum((inputs_i - mean)^2) / (n-1) )   (n >= 2)",
  VARIANCE_OF:    "sum((inputs_i - mean)^2) / (n-1)           (n >= 2)",
  COUNT_GOOD:     "count(inputs_i where quality = GOOD)",
  COUNT_NONZERO:  "count(inputs_i where inputs_i != 0)",
  WEIGHTED_AVG:   "sum(weights_i * inputs_i) / sum(weights)",

  // ── Selection / redundancy ───────────────────────────────────────
  FIRST_GOOD:      "first inputs_i with GOOD quality (scan in order)",
  LAST_GOOD:       "last inputs_i with GOOD quality (scan in reverse)",
  HIGHEST_QUALITY: "inputs_i with the highest quality byte",
  HOT_STANDBY:     "primary if primary is GOOD, else standby",
  VOTING_M_OF_N:   "value of the agreeing cluster when >= min_agreement inputs are within tolerance, else BAD",
  MUX_INDEX:       "inputs[index]                         (index is 0-based)",

  // ── Conditional / comparison / logic ─────────────────────────────
  IF_THEN_ELSE: "(condition > 0) ? then_value : else_value",
  GT:  "(left > right) ? 1 : 0",
  GTE: "(left >= right) ? 1 : 0",
  LT:  "(left < right) ? 1 : 0",
  LTE: "(left <= right) ? 1 : 0",
  EQ:  "(abs(left - right) <= tolerance) ? 1 : 0",
  NE:  "(abs(left - right) > tolerance) ? 1 : 0",
  AND_OF: "1 if all inputs > 0, else 0",
  OR_OF:  "1 if any input > 0, else 0",
  XOR_OF: "1 if an odd number of inputs are > 0, else 0",
  NOT:    "1 if input <= 0, else 0",

  // ── Stateful (IEC 61131-3) ───────────────────────────────────────
  TON:    "1 after input has been TRUE for preset_ms (on-delay)",
  TOF:    "1 while input TRUE; holds 1 for preset_ms after input goes FALSE (off-delay)",
  TP:     "1 for preset_ms on input's rising edge (pulse)",
  R_TRIG: "1 for one scan when input goes FALSE -> TRUE (rising edge)",
  F_TRIG: "1 for one scan when input goes TRUE -> FALSE (falling edge)",
  SR:     "set/reset latch, set-dominant: set -> 1, reset -> 0, set wins ties",
  RS:     "set/reset latch, reset-dominant: set -> 1, reset -> 0, reset wins ties",
  CTU:    "counts up on each count_up rising edge; reset -> 0",
  CTD:    "counts down on each count_down rising edge; load -> load_value",
};

/** Reference equation for a block code, or null if none is defined. */
export function blockExample(code: string | null | undefined): string | null {
  if (!code) return null;
  return BLOCK_EXAMPLES[code] ?? null;
}
