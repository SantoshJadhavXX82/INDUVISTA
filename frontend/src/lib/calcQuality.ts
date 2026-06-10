/**
 * Calc quality decoder.
 *
 * The calc evaluator emits an OPC-style quality BYTE (0..255) on every
 * computed output (see backend/app/workers/calc_blocks/base.py):
 *
 *   GOOD_QUALITY        = 128   // at/above this the value is usable
 *   GOOD_NON_SPECIFIC   = 192   // clean value, no caveats
 *   BAD_QUALITY         = 0     // unusable (also ST_BAD for NaN/Inf)
 *
 * Convention:
 *   >= 128         GOOD       — usable
 *   64 .. 127      UNCERTAIN  — value present, treat with care
 *   <  64          BAD        — do not use
 *   null/undefined NO DATA    — nothing reported yet
 *
 * This file is pure logic (no JSX) so it can be reused by rows, the
 * header banner, drawers, etc. Callers render the dot/label themselves
 * using the returned Tailwind class names.
 */

export type QualityKey = "good" | "uncertain" | "bad" | "nodata";

export interface QualityMeta {
  key: QualityKey;
  /** Short human label, e.g. "Good". */
  label: string;
  /** Tailwind bg class for a status dot, e.g. "bg-emerald-500". */
  dot: string;
  /** Tailwind text class for inline text, e.g. "text-emerald-700". */
  text: string;
  /** The raw byte (or null), preserved for tooltips. */
  raw: number | null;
}

export const GOOD_QUALITY = 128;
export const GOOD_NON_SPECIFIC = 192;
export const UNCERTAIN_QUALITY = 64;

const META: Record<QualityKey, Omit<QualityMeta, "raw">> = {
  good:      { key: "good",      label: "Good",      dot: "bg-emerald-500", text: "text-emerald-700" },
  uncertain: { key: "uncertain", label: "Uncertain", dot: "bg-amber-500",   text: "text-amber-700" },
  bad:       { key: "bad",       label: "Bad",       dot: "bg-red-500",     text: "text-red-700" },
  nodata:    { key: "nodata",    label: "No data",   dot: "bg-slate-300",   text: "text-slate-500" },
};

/** Decode a raw quality byte into display metadata. */
export function decodeQuality(q: number | null | undefined): QualityMeta {
  if (q === null || q === undefined || Number.isNaN(q)) {
    return { ...META.nodata, raw: null };
  }
  let key: QualityKey;
  if (q >= GOOD_QUALITY) key = "good";
  else if (q >= UNCERTAIN_QUALITY) key = "uncertain";
  else key = "bad";
  return { ...META[key], raw: q };
}

/** Tooltip string, e.g. "Good (quality 192)" or "No data". */
export function describeQuality(q: number | null | undefined): string {
  const m = decodeQuality(q);
  return m.raw === null ? m.label : `${m.label} (quality ${m.raw})`;
}
