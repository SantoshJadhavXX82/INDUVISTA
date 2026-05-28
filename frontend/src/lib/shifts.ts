/**
 * Shift computation (Phase 27e). Given the shift config and a Date,
 * returns the currently-active shift. Shifts are contiguous and ordered
 * by start time; the last shift wraps past midnight into the first.
 *
 * Times in the config are plant-local HH:MM. We compare against the
 * supplied Date's local hours/minutes — the caller is responsible for
 * passing a Date already in the desired zone (the sidebar uses the
 * browser clock, which for a single-site plant is the plant clock).
 */
export type Shift = { code: string; label: string; start: string };
export type ShiftsConfig = { enabled: boolean; shifts: Shift[] };

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

/** Returns the active shift for `now`, or null if shifts are disabled/empty. */
export function currentShift(cfg: ShiftsConfig | undefined, now: Date): Shift | null {
  if (!cfg || !cfg.enabled || !cfg.shifts?.length) return null;
  const sorted = [...cfg.shifts].sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
  const mins = now.getHours() * 60 + now.getMinutes();

  // Find the last shift whose start <= now. If none (before the first
  // shift's start), it's still the LAST shift from yesterday (wrap).
  let active: Shift = sorted[sorted.length - 1];
  for (const s of sorted) {
    if (mins >= toMinutes(s.start)) active = s;
    else break;
  }
  return active;
}

/** ISO week number (1–53), per ISO-8601 (week with the year's first Thursday). */
export function isoWeek(date: Date): number {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = (d.getUTCDay() + 6) % 7;        // Mon=0..Sun=6
  d.setUTCDate(d.getUTCDate() - dayNum + 3);      // nearest Thursday
  const firstThursday = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
  const firstDayNum = (firstThursday.getUTCDay() + 6) % 7;
  firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNum + 3);
  return 1 + Math.round((d.getTime() - firstThursday.getTime()) / (7 * 24 * 3600 * 1000));
}

/** "14d 6h" style compact uptime from seconds. */
export function formatUptimeShort(sec: number): string {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}
