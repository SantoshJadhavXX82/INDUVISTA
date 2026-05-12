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
