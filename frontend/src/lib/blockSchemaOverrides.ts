/**
 * Phase 17.0a - Frontend overrides for backend calc block schemas.
 *
 * The backend's /api/calc/block-schemas endpoint returns generic field
 * labels like "Left Operand"/"Right Operand" and some inputs are tag-only
 * when they could logically also be constants. Fixing this at the
 * backend means editing 60+ schemas in calc_block_schemas.py, which
 * is a Phase 17.0b task. Until then, this file lets us patch labels
 * and field types per block-type from the frontend.
 *
 * Each entry is { fieldKey -> { label?, help?, allow_constant? } }.
 *   - label / help: replace the schema's defaults
 *   - allow_constant: upgrade a tag_ref field to tag_or_constant
 *                     (or tag_ref_list to tag_or_constant_list)
 *
 * IMPORTANT - VERIFY FIELD KEYS:
 * The field keys below are guesses based on common conventions. To
 * see the actual keys for your schemas, paste in the browser console:
 *
 *   fetch('/api/calc/block-schemas').then(r => r.json()).then(s =>
 *     Object.entries(s).forEach(([code, schema]) =>
 *       console.log(code, schema.fields.map(f =>
 *         f.type === 'mode_select'
 *           ? `${f.key}[mode_select]:` + (f.options || []).map(o => `${o.value}=>${o.fields.map(sf => sf.key).join(',')}`).join(' | ')
 *           : `${f.key}(${f.type})`
 *       ))));
 *
 * Then adjust the keys in this file. Any unrecognized key is silently
 * ignored - it just means no override applies.
 *
 * TODO Phase 17.0b: extend backend block_schemas to carry these labels
 * declaratively, then delete this file.
 */

export interface FieldOverride {
  label?: string;
  help?: string;
  /** If true, upgrade tag_ref -> tag_or_constant (and list variant). */
  allow_constant?: boolean;
}

export const BLOCK_SCHEMA_OVERRIDES: Record<string, Record<string, FieldOverride>> = {

  // ===== Arithmetic - binary =====
  // Many of these may use a mode_select for binary vs n-ary. Sub-fields
  // get recursively overridden too, so the same keys apply inside modes.
  ADD: {
    left:   { label: "Operand 1", allow_constant: true },
    right:  { label: "Operand 2", allow_constant: true },
    inputs: { label: "Operands",  allow_constant: true },
  },
  SUB: {
    left:  { label: "Minuend (start with)",       allow_constant: true },
    right: { label: "Subtrahend (subtract this)", allow_constant: true },
  },
  MUL: {
    left:   { label: "Factor 1", allow_constant: true },
    right:  { label: "Factor 2", allow_constant: true },
    inputs: { label: "Factors",  allow_constant: true },
  },
  DIV: {
    left:  { label: "Numerator",   allow_constant: true },
    right: { label: "Denominator", allow_constant: true },
  },
  MOD: {
    left:  { label: "Dividend", allow_constant: true },
    right: { label: "Divisor",  allow_constant: true },
  },
  POW: {
    left:  { label: "Base",     allow_constant: true },
    right: { label: "Exponent", allow_constant: true },
  },
  NEG: {
    input: { label: "Value to negate", allow_constant: true },
  },

  // ===== Unary math =====
  ABS:   { input: { label: "Value", allow_constant: true } },
  CEIL:  { input: { label: "Value", allow_constant: true } },
  FLOOR: { input: { label: "Value", allow_constant: true } },
  ROUND: { input: { label: "Value", allow_constant: true } },
  SQRT:  { input: { label: "Value", allow_constant: true } },

  // ===== Transcendental =====
  EXP:   { input: { label: "Value", allow_constant: true } },
  LN:    { input: { label: "Value", allow_constant: true } },
  LOG10: { input: { label: "Value", allow_constant: true } },
  SIN:   { input: { label: "Angle (radians)", allow_constant: true } },
  COS:   { input: { label: "Angle (radians)", allow_constant: true } },
  TAN:   { input: { label: "Angle (radians)", allow_constant: true } },

  // ===== Comparison =====
  EQ: {
    left:  { label: "Value",        allow_constant: true },
    right: { label: "Compared with", allow_constant: true },
  },
  NE: {
    left:  { label: "Value",        allow_constant: true },
    right: { label: "Compared with", allow_constant: true },
  },
  GT: {
    left:  { label: "Value",     allow_constant: true },
    right: { label: "Threshold (must be greater than)", allow_constant: true },
  },
  GTE: {
    left:  { label: "Value",     allow_constant: true },
    right: { label: "Threshold (must be >=)", allow_constant: true },
  },
  LT: {
    left:  { label: "Value",     allow_constant: true },
    right: { label: "Threshold (must be less than)", allow_constant: true },
  },
  LTE: {
    left:  { label: "Value",     allow_constant: true },
    right: { label: "Threshold (must be <=)", allow_constant: true },
  },

  // ===== Logical =====
  AND_OF: { inputs: { label: "Boolean inputs", allow_constant: true } },
  OR_OF:  { inputs: { label: "Boolean inputs", allow_constant: true } },
  XOR_OF: { inputs: { label: "Boolean inputs", allow_constant: true } },
  NOT:    { input:  { label: "Boolean to invert", allow_constant: true } },

  // ===== Aggregation =====
  AVG_OF:          { inputs: { label: "Values to average",            allow_constant: true } },
  SUM_OF:          { inputs: { label: "Values to sum",                allow_constant: true } },
  PRODUCT_OF:      { inputs: { label: "Values to multiply",           allow_constant: true } },
  MIN_OF:          { inputs: { label: "Values (pick minimum)",        allow_constant: true } },
  MAX_OF:          { inputs: { label: "Values (pick maximum)",        allow_constant: true } },
  MEDIAN_OF:       { inputs: { label: "Values (pick median)",         allow_constant: true } },
  MODE_OF:         { inputs: { label: "Values (pick mode)",           allow_constant: true } },
  RANGE_OF:        { inputs: { label: "Values (max - min)",           allow_constant: true } },
  RMS_OF:          { inputs: { label: "Values (root-mean-square)",    allow_constant: true } },
  STDDEV_OF:       { inputs: { label: "Values (standard deviation)",  allow_constant: true } },
  VARIANCE_OF:     { inputs: { label: "Values (variance)",            allow_constant: true } },
  GEOMETRIC_MEAN:  { inputs: { label: "Values (geometric mean)",      allow_constant: true } },
  HARMONIC_MEAN:   { inputs: { label: "Values (harmonic mean)",       allow_constant: true } },
  COUNT_GOOD:      { inputs: { label: "Inputs to scan for good quality", allow_constant: true } },
  COUNT_NONZERO:   { inputs: { label: "Inputs to scan for nonzero",   allow_constant: true } },
  FIRST_GOOD:      { inputs: { label: "Candidates (use first good)",  allow_constant: true } },
  LAST_GOOD:       { inputs: { label: "Candidates (use last good)",   allow_constant: true } },
  HIGHEST_QUALITY: { inputs: { label: "Candidates (use best quality)", allow_constant: true } },
  WEIGHTED_AVG: {
    values:  { label: "Values",            allow_constant: true },
    weights: { label: "Weights",           allow_constant: true },
    inputs:  { label: "Values & weights",  allow_constant: true },
  },

  // ===== Selection =====
  MIN_OF_TWO: {
    left:  { label: "Value 1", allow_constant: true },
    right: { label: "Value 2", allow_constant: true },
    a:     { label: "Value 1", allow_constant: true },
    b:     { label: "Value 2", allow_constant: true },
  },
  MAX_OF_TWO: {
    left:  { label: "Value 1", allow_constant: true },
    right: { label: "Value 2", allow_constant: true },
    a:     { label: "Value 1", allow_constant: true },
    b:     { label: "Value 2", allow_constant: true },
  },
  MUX_INDEX: {
    index:  { label: "Index (selects which input)", allow_constant: true },
    inputs: { label: "Candidate values",            allow_constant: true },
    values: { label: "Candidate values",            allow_constant: true },
  },
  HOT_STANDBY: {
    primary: { label: "Primary (use when good)",          allow_constant: true },
    backup:  { label: "Backup (use when primary is bad)",  allow_constant: true },
    standby: { label: "Standby (use when primary is bad)", allow_constant: true },
  },
  VOTING_M_OF_N: {
    inputs: { label: "Boolean inputs to vote on", allow_constant: true },
    m:      { label: "M (votes required)" },
  },

  // ===== Conditional =====
  IF_THEN_ELSE: {
    condition:    { label: "If this is true...",    allow_constant: true },
    if_input:     { label: "If this is true...",    allow_constant: true },
    then_input:   { label: "...use this value",     allow_constant: true },
    else_input:   { label: "...otherwise use this", allow_constant: true },
    true_value:   { label: "...use this value",     allow_constant: true },
    false_value:  { label: "...otherwise use this", allow_constant: true },
    when_true:    { label: "...use this value",     allow_constant: true },
    when_false:   { label: "...otherwise use this", allow_constant: true },
    then_value:   { label: "...use this value",     allow_constant: true },
    else_value:   { label: "...otherwise use this", allow_constant: true },
  },

  // ===== Timers — boolean input ('0' or non-zero constant for always-OFF/ON) =====
  TON: {
    input:           { label: "Enable signal", allow_constant: true },
    preset_sec:      { label: "Delay before output goes ON (seconds)" },
    preset_time_sec: { label: "Delay before output goes ON (seconds)" },
    preset_ms:       { label: "Delay before output goes ON (ms)" },
  },
  TOF: {
    input:           { label: "Enable signal", allow_constant: true },
    preset_sec:      { label: "Delay before output goes OFF (seconds)" },
    preset_time_sec: { label: "Delay before output goes OFF (seconds)" },
    preset_ms:       { label: "Delay before output goes OFF (ms)" },
  },
  TP: {
    input:           { label: "Trigger signal", allow_constant: true },
    preset_sec:      { label: "Pulse duration (seconds)" },
    preset_time_sec: { label: "Pulse duration (seconds)" },
    preset_ms:       { label: "Pulse duration (ms)" },
  },

  // ===== Edge detectors =====
  R_TRIG: { input: { label: "Signal (one-shot on rising edge)",  allow_constant: true } },
  F_TRIG: { input: { label: "Signal (one-shot on falling edge)", allow_constant: true } },

  // ===== Latches =====
  SR: {
    set:   { label: "Set (S has priority)", allow_constant: true },
    reset: { label: "Reset",                allow_constant: true },
    s:     { label: "Set (S has priority)", allow_constant: true },
    r:     { label: "Reset",                allow_constant: true },
  },
  RS: {
    set:   { label: "Set",                   allow_constant: true },
    reset: { label: "Reset (R has priority)", allow_constant: true },
    s:     { label: "Set",                   allow_constant: true },
    r:     { label: "Reset (R has priority)", allow_constant: true },
  },

  // ===== Counters =====
  CTU: {
    count_input:  { label: "Count input (counts on rising edge)", allow_constant: true },
    reset_input:  { label: "Reset to 0",                          allow_constant: true },
    count_up:     { label: "Count input (counts on rising edge)", allow_constant: true },
    cu:           { label: "Count input (counts on rising edge)", allow_constant: true },
    reset:        { label: "Reset to 0",                          allow_constant: true },
    preset:       { label: "Preset count (output goes ON at this value)" },
    preset_value: { label: "Preset count (output goes ON at this value)" },
  },
  CTD: {
    count_input:  { label: "Count input (counts down on rising edge)", allow_constant: true },
    load_input:   { label: "Load preset value into counter",            allow_constant: true },
    count_down:   { label: "Count input (counts down on rising edge)", allow_constant: true },
    cd:           { label: "Count input (counts down on rising edge)", allow_constant: true },
    load:         { label: "Load preset value into counter",            allow_constant: true },
    ld:           { label: "Load preset value into counter",            allow_constant: true },
    preset:       { label: "Preset count (start counting down from)" },
    preset_value: { label: "Preset count (start counting down from)" },
  },

};


// ---------------------------------------------------------------------------
// Apply helpers (consumed by CalcBlockForm)
// ---------------------------------------------------------------------------

import type { FieldDef, ModeOption } from "@/types/calcBlockSchemas";


/** Apply overrides to one field. Recurses into mode_select sub-fields. */
function applyOverridesToField(
  field: FieldDef,
  overrides: Record<string, FieldOverride>,
): FieldDef {
  let result: FieldDef = { ...field };
  const ov = overrides[field.key];

  if (ov) {
    if (ov.label !== undefined) result.label = ov.label;
    if (ov.help !== undefined)  result.help  = ov.help;
    if (ov.allow_constant) {
      if (result.type === "tag_ref") {
        result.type = "tag_or_constant";
      } else if (result.type === "tag_ref_list") {
        result.type = "tag_or_constant_list";
      }
    }
  }

  // Recurse into mode_select sub-fields. mode_select uses
  // ModeOption[] (with nested .fields), distinct from enum's
  // EnumOption[] (which has no .fields).
  if (result.type === "mode_select" && Array.isArray(result.options)) {
    const opts = result.options as ModeOption[];
    if (opts.length > 0 && "fields" in opts[0]) {
      result = {
        ...result,
        options: opts.map((mode) => ({
          ...mode,
          fields: mode.fields.map((sub) => applyOverridesToField(sub, overrides)),
        })),
      };
    }
  }

  return result;
}


/**
 * Apply per-block-type overrides to a schema's fields. Pure function -
 * returns a new array, doesn't mutate the input.
 */
export function applyBlockOverrides(
  blockCode: string,
  fields: FieldDef[],
): FieldDef[] {
  const overrides = BLOCK_SCHEMA_OVERRIDES[blockCode];
  if (!overrides) return fields;
  return fields.map((f) => applyOverridesToField(f, overrides));
}
