/**
 * Phase 13.2 + 13.5 — Time range picker for the Trend page.
 *
 * Two tabs:
 *   - Presets: spec §7.1 list — Last 5m/15m/1h/8h/24h, Today, Yesterday,
 *     Current/Previous Week, Current/Previous Month
 *   - Custom:  two datetime-local inputs (timezone-naive browser local time)
 *
 * Always emits UTC ISO strings regardless of browser timezone (spec §7.2:
 * UTC storage, local display).
 *
 * Week math: Monday-start (ISO 8601), conventional in IN/EU/Asia. The
 * WEEK_START_DAY constant is the single knob to flip for Sunday-start.
 */
import { useState } from "react";
import { Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type TimeRange = {
  start: string;       // ISO UTC
  end: string;         // ISO UTC
  label: string;       // human-readable label for the toolbar pill
};

type TimeRangePickerProps = {
  value: TimeRange;
  onChange: (range: TimeRange) => void;
};

// Rolling presets (relative to now) work seamlessly with saved-view recall.
// Date-anchored presets (today/yesterday/week/month) save as absolute when
// stored in a view, because "today" tomorrow means a different absolute day.
type PresetKey =
  | "5m" | "15m" | "1h" | "8h" | "24h"
  | "today" | "yesterday"
  | "current-week" | "previous-week"
  | "current-month" | "previous-month";

type Preset = { key: PresetKey; label: string; rollingMinutes?: number };

const PRESETS: Preset[] = [
  { key: "5m",              label: "Last 5 min",      rollingMinutes: 5 },
  { key: "15m",             label: "Last 15 min",     rollingMinutes: 15 },
  { key: "1h",              label: "Last 1 h",        rollingMinutes: 60 },
  { key: "8h",              label: "Last 8 h",        rollingMinutes: 480 },
  { key: "24h",             label: "Last 24 h",       rollingMinutes: 1440 },
  { key: "today",           label: "Today" },
  { key: "yesterday",       label: "Yesterday" },
  { key: "current-week",    label: "Current week" },
  { key: "previous-week",   label: "Previous week" },
  { key: "current-month",   label: "Current month" },
  { key: "previous-month",  label: "Previous month" },
];

const WEEK_START_DAY = 1; // 0 = Sun, 1 = Mon

/**
 * Rolling preset anchored to NOW. Exported so saved-view recall in Trend.tsx
 * can recompute the window against the current moment on load.
 */
export function makePresetRange(minutes: number, label: string): TimeRange {
  const end = new Date();
  const start = new Date(end.getTime() - minutes * 60_000);
  return { start: start.toISOString(), end: end.toISOString(), label };
}

/** Date-anchored preset — absolute bounds based on operator's local calendar. */
function makeDatePresetRange(key: PresetKey, label: string): TimeRange {
  const now = new Date();
  const y = now.getFullYear(), m = now.getMonth(), d = now.getDate();
  let start: Date, end: Date;

  switch (key) {
    case "today":
      start = new Date(y, m, d);
      end = now;
      break;
    case "yesterday":
      start = new Date(y, m, d - 1);
      end = new Date(y, m, d);
      break;
    case "current-week": {
      const daysSinceStart = (now.getDay() - WEEK_START_DAY + 7) % 7;
      start = new Date(y, m, d - daysSinceStart);
      end = now;
      break;
    }
    case "previous-week": {
      const daysSinceStart = (now.getDay() - WEEK_START_DAY + 7) % 7;
      start = new Date(y, m, d - daysSinceStart - 7);
      end = new Date(y, m, d - daysSinceStart);
      break;
    }
    case "current-month":
      start = new Date(y, m, 1);
      end = now;
      break;
    case "previous-month":
      start = new Date(y, m - 1, 1);
      end = new Date(y, m, 1);
      break;
    default:
      return makePresetRange(60, label);
  }
  return { start: start.toISOString(), end: end.toISOString(), label };
}

function makeRangeFromPreset(preset: Preset): TimeRange {
  if (preset.rollingMinutes != null) {
    return makePresetRange(preset.rollingMinutes, preset.label);
  }
  return makeDatePresetRange(preset.key, preset.label);
}

export default function TimeRangePicker({ value, onChange }: TimeRangePickerProps) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"preset" | "custom">("preset");
  const [customStart, setCustomStart] = useState<string>(toLocalInput(value.start));
  const [customEnd,   setCustomEnd]   = useState<string>(toLocalInput(value.end));

  const applyPreset = (preset: Preset) => {
    onChange(makeRangeFromPreset(preset));
    setOpen(false);
  };

  const applyCustom = () => {
    if (!customStart || !customEnd) return;
    const startUtc = new Date(customStart).toISOString();
    const endUtc   = new Date(customEnd).toISOString();
    if (startUtc >= endUtc) return;
    onChange({ start: startUtc, end: endUtc, label: "Custom range" });
    setOpen(false);
  };

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => setOpen((v) => !v)}
      >
        <Clock className="h-3 w-3" />
        {value.label}
      </Button>

      {open && (
        <div className="absolute left-0 top-full mt-1 w-[280px] z-50 bg-card border border-border rounded-md shadow-lg">
          <div className="flex border-b border-border text-xs">
            <button
              type="button"
              className={`flex-1 py-2 ${mode === "preset" ? "font-semibold border-b-2 border-primary" : "text-muted-foreground"}`}
              onClick={() => setMode("preset")}
            >
              Presets
            </button>
            <button
              type="button"
              className={`flex-1 py-2 ${mode === "custom" ? "font-semibold border-b-2 border-primary" : "text-muted-foreground"}`}
              onClick={() => setMode("custom")}
            >
              Custom
            </button>
          </div>

          {mode === "preset" && (
            <div className="p-2 max-h-[340px] overflow-y-auto">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pt-1 pb-0.5">
                Rolling
              </div>
              <div className="grid grid-cols-2 gap-1 mb-2">
                {PRESETS.filter((p) => p.rollingMinutes != null).map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    onClick={() => applyPreset(p)}
                    className="text-left px-3 py-2 rounded text-xs hover:bg-secondary/40"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pt-1 pb-0.5">
                Date-anchored
              </div>
              <div className="grid grid-cols-2 gap-1">
                {PRESETS.filter((p) => p.rollingMinutes == null).map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    onClick={() => applyPreset(p)}
                    className="text-left px-3 py-2 rounded text-xs hover:bg-secondary/40"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {mode === "custom" && (
            <div className="p-3 space-y-2">
              <div className="space-y-1">
                <Label htmlFor="ts-start" className="text-xs">From (local time)</Label>
                <Input
                  id="ts-start"
                  type="datetime-local"
                  value={customStart}
                  onChange={(e) => setCustomStart(e.target.value)}
                  className="h-8 text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ts-end" className="text-xs">To (local time)</Label>
                <Input
                  id="ts-end"
                  type="datetime-local"
                  value={customEnd}
                  onChange={(e) => setCustomEnd(e.target.value)}
                  className="h-8 text-xs"
                />
              </div>
              <div className="flex gap-2 pt-1">
                <Button size="sm" className="text-xs h-7 flex-1" onClick={applyCustom}>
                  Apply
                </Button>
                <Button
                  size="sm" variant="outline"
                  className="text-xs h-7"
                  onClick={() => setOpen(false)}
                >
                  Cancel
                </Button>
              </div>
              <p className="text-[10px] text-muted-foreground pt-1">
                Times sent to backend as UTC ({Intl.DateTimeFormat().resolvedOptions().timeZone} → UTC).
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
