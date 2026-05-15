/**
 * Phase 13.6 - Time format preference (24h / 12h / System auto).
 *
 * Industrial convention is 24-hour, so that's the default. Operators in
 * some markets prefer 12-hour; "Auto" follows the browser's resolved
 * locale (Intl.DateTimeFormat).
 *
 * Preference persists to localStorage so it survives reload. All time
 * displays across the trend module should call the helpers in this hook
 * instead of formatting dates ad-hoc - this keeps the entire UI in sync
 * when the user flips the toggle.
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from "react";

export type TimeFormatMode = "auto" | "24h" | "12h";

const STORAGE_KEY = "induvista.timeFormat";

type TimeFormatContextValue = {
  mode: TimeFormatMode;
  setMode: (m: TimeFormatMode) => void;
  /** Resolved boolean - true if currently displaying as 24-hour. */
  is24h: boolean;
  /** HH:MM:SS  or  h:MM:SS AM/PM */
  formatTime: (date: Date | string | number) => string;
  /** HH:MM  or  h:MM AM/PM (no seconds, for compact display) */
  formatTimeShort: (date: Date | string | number) => string;
  /** YYYY-MM-DD HH:MM:SS  or  YYYY-MM-DD h:MM:SS AM/PM */
  formatDateTime: (date: Date | string | number) => string;
  /** YYYY-MM-DD (no time) */
  formatDate: (date: Date | string | number) => string;
};

const TimeFormatContext = createContext<TimeFormatContextValue | null>(null);

/** True if the browser locale resolves to 24-hour clock. */
function resolveSystem24h(): boolean {
  try {
    const opts = new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions();
    // hourCycle h23/h24 = 24h. h11/h12 = 12h. hour12 fallback for older runtimes.
    if (opts.hourCycle) return opts.hourCycle === "h23" || opts.hourCycle === "h24";
    return opts.hour12 === false;
  } catch {
    return true; // fall back to 24h if Intl somehow fails
  }
}

function resolveIs24h(mode: TimeFormatMode): boolean {
  if (mode === "24h") return true;
  if (mode === "12h") return false;
  return resolveSystem24h();
}

const pad = (n: number) => String(n).padStart(2, "0");

function formatTimeImpl(d: Date, is24h: boolean, withSeconds: boolean): string {
  if (is24h) {
    const base = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return withSeconds ? `${base}:${pad(d.getSeconds())}` : base;
  }
  // 12-hour
  const h24 = d.getHours();
  const period = h24 >= 12 ? "PM" : "AM";
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  const base = `${h12}:${pad(d.getMinutes())}`;
  return withSeconds
    ? `${base}:${pad(d.getSeconds())} ${period}`
    : `${base} ${period}`;
}

function formatDateImpl(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function loadStoredMode(): TimeFormatMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "24h" || stored === "12h" || stored === "auto") return stored;
  } catch {
    // localStorage blocked or unavailable - fall through to default
  }
  return "24h"; // industrial default
}

export function TimeFormatProvider({ children }: { children: ReactNode }) {
  const [mode, setModeRaw] = useState<TimeFormatMode>(loadStoredMode);

  const setMode = useCallback((m: TimeFormatMode) => {
    setModeRaw(m);
    try {
      localStorage.setItem(STORAGE_KEY, m);
    } catch {
      // Persistence is best-effort - if blocked (incognito etc.) the
      // setting still works for the current session.
    }
  }, []);

  // Re-resolve on mode change. Memoized so React.memo'd children can
  // skip re-renders when the toggle isn't touched.
  const value = useMemo<TimeFormatContextValue>(() => {
    const is24h = resolveIs24h(mode);
    const toDate = (d: Date | string | number) => (d instanceof Date ? d : new Date(d));
    return {
      mode,
      setMode,
      is24h,
      formatTime:      (d) => formatTimeImpl(toDate(d), is24h, true),
      formatTimeShort: (d) => formatTimeImpl(toDate(d), is24h, false),
      formatDateTime:  (d) => {
        const date = toDate(d);
        return `${formatDateImpl(date)} ${formatTimeImpl(date, is24h, true)}`;
      },
      formatDate:      (d) => formatDateImpl(toDate(d)),
    };
  }, [mode, setMode]);

  // Some "auto" mode users have OS-level locale changes that aren't
  // observable from the page. We re-resolve on focus to catch them.
  useEffect(() => {
    if (mode !== "auto") return;
    const handler = () => setModeRaw("auto"); // forces value recompute
    window.addEventListener("focus", handler);
    return () => window.removeEventListener("focus", handler);
  }, [mode]);

  return (
    <TimeFormatContext.Provider value={value}>
      {children}
    </TimeFormatContext.Provider>
  );
}

export function useTimeFormat(): TimeFormatContextValue {
  const ctx = useContext(TimeFormatContext);
  if (!ctx) {
    throw new Error("useTimeFormat must be used inside <TimeFormatProvider>");
  }
  return ctx;
}
