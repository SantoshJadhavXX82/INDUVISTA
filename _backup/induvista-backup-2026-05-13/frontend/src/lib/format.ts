/**
 * Locale-aware date/time formatting helpers.
 *
 * All functions delegate to `toLocaleString(undefined, …)` — the
 * `undefined` locale arg makes the browser fall back to its configured
 * locale (navigator.language). The user gets:
 *
 *   en-IN / en-GB  →  DD/MM/YYYY 14:30
 *   en-US          →  MM/DD/YYYY 02:30 PM
 *   de-DE          →  DD.MM.YYYY, 14:30
 *   ja-JP          →  YYYY/MM/DD 14:30
 *
 * Year is forced to 4-digit (numeric) for clarity in audit data. 12 vs
 * 24-hour clock is left to the locale — `en-IN` typically renders 24h,
 * `en-US` 12h with AM/PM. Override with the optional `hour12` field on
 * the underlying options object if needed.
 *
 * Use the `WithSeconds` variants for audit-style displays where two
 * writes within the same minute need to be distinguishable.
 */

const DATE_OPTS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
};

const DATE_TIME_OPTS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
};

const DATE_TIME_SEC_OPTS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
};

const TIME_OPTS: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
};

const TIME_SEC_OPTS: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
};

function toDate(v: string | Date): Date {
  return typeof v === "string" ? new Date(v) : v;
}

/** DD/MM/YYYY HH:MM (locale-respecting). For general displays. */
export function formatDateTime(v: string | Date | null | undefined): string {
  if (v == null) return "—";
  return toDate(v).toLocaleString(undefined, DATE_TIME_OPTS);
}

/** DD/MM/YYYY HH:MM:SS — for audit-grade displays where seconds matter. */
export function formatDateTimeWithSeconds(
  v: string | Date | null | undefined,
): string {
  if (v == null) return "—";
  return toDate(v).toLocaleString(undefined, DATE_TIME_SEC_OPTS);
}

/** DD/MM/YYYY (date only). */
export function formatDate(v: string | Date | null | undefined): string {
  if (v == null) return "—";
  return toDate(v).toLocaleDateString(undefined, DATE_OPTS);
}

/** HH:MM (time only). */
export function formatTime(v: string | Date | null | undefined): string {
  if (v == null) return "—";
  return toDate(v).toLocaleTimeString(undefined, TIME_OPTS);
}

/** HH:MM:SS (time only, with seconds). */
export function formatTimeWithSeconds(
  v: string | Date | null | undefined,
): string {
  if (v == null) return "—";
  return toDate(v).toLocaleTimeString(undefined, TIME_SEC_OPTS);
}

/**
 * Relative-time string: "5s ago", "2m ago", "3h ago", "yesterday", etc.
 * Falls back to the locale-formatted absolute date for older items.
 */
export function formatRelative(v: string | Date | null | undefined): string {
  if (v == null) return "—";
  const then = toDate(v).getTime();
  const now = Date.now();
  const sec = Math.round((now - then) / 1000);
  if (sec < 0) return "just now";
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return formatDateTime(v);
}


// ===========================================================================
// Number formatting
// ===========================================================================

/**
 * Format a finite floating-point number for display.
 *
 * Threshold philosophy:
 *
 *   • Scientific notation is reserved for values that are genuinely too
 *     large or too small to read as plain decimals — abs ≥ 1e7 or, when
 *     non-zero, abs < 1e-4. Everything in between renders as a regular
 *     decimal, so 0.01, 0.001, 94.9, and 12345 all show as themselves.
 *
 *   • Float32 precision quirk: Daniel's "0.01" arrives on the wire as
 *     IEEE 754 float32 0x3C23D70A = 0.009999999776482582 — strictly less
 *     than 0.01 in float64 arithmetic. A naive "abs < 0.01 → toExponential"
 *     branch trips on this and emits "1.00e-2". This formatter rounds with
 *     toFixed(4) instead, so 0.009999… → "0.0100" → "0.01" after trailing-
 *     zero strip.
 *
 *   • Trailing zeros are stripped for readability: "94.9000" → "94.9",
 *     "0.0100" → "0.01", "1234.00" → "1234". This is purely cosmetic;
 *     the underlying value is unchanged.
 *
 * Decimal-count tiering is coarser for big numbers and finer for small
 * ones, keeping roughly four significant digits across the range.
 */
function formatFloatMagnitude(d: number): string {
  const abs = Math.abs(d);
  if (abs === 0) return "0";

  // Only truly extreme values get scientific notation.
  if (abs >= 1e7) return d.toExponential(3);
  if (abs < 1e-4) return d.toExponential(3);

  // Pick a decimal count that gives ~4 significant digits.
  let decimals: number;
  if (abs >= 1000) decimals = 2;         // 12345.67
  else if (abs >= 10) decimals = 3;      // 94.900 → 94.9
  else if (abs >= 1) decimals = 4;       // 2.5000 → 2.5
  else if (abs >= 0.01) decimals = 4;    // 0.0100 → 0.01 (float32 0.01 fix)
  else decimals = 5;                     // 0.0001 → 0.0001

  const s = d.toFixed(decimals);
  // Strip trailing zeros after the decimal point, then a lone trailing dot.
  //   "94.9000" → "94.9"   "0.0100" → "0.01"   "1234.00" → "1234"
  return s.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

/**
 * Format a raw floating-point number. Returns "—" for NaN/Infinity.
 * Used for register-browser decoded views where there is no tag type.
 */
export function formatFloat(f: number | null | undefined): string {
  if (f == null) return "—";
  if (!Number.isFinite(f)) return "—";
  return formatFloatMagnitude(f);
}

/**
 * Format a tag's display value, taking its data_type into account.
 *
 *   • If a text representation is available (engineering-unit string,
 *     named-set label), that wins.
 *   • bool → "TRUE" / "FALSE".
 *   • int* / uint* → truncated integer string.
 *   • float* → routed through formatFloatMagnitude.
 *   • Null/undefined value → "—".
 */
export function formatTagValue(
  d: number | null | undefined,
  text: string | null | undefined,
  dataType: string,
): string {
  if (text !== null && text !== undefined) return text;
  if (d === null || d === undefined) return "—";
  if (dataType === "bool") return d ? "TRUE" : "FALSE";
  if (dataType.startsWith("int") || dataType.startsWith("uint")) {
    return Math.trunc(d).toString();
  }
  if (!Number.isFinite(d)) return "—";
  return formatFloatMagnitude(d);
}
