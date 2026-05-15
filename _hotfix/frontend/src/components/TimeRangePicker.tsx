/**
 * Phase 13.2 — Time range picker for the Trend page.
 *
 * Two modes:
 *   - Preset:  Last 15m / 1h / 6h / 24h / 7d / 30d (rolling, end=now)
 *   - Custom:  two datetime-local inputs (timezone-naive browser local time)
 *
 * The component always emits UTC ISO strings, regardless of the user's local
 * timezone. Spec §7.2 — UTC storage, local display.
 */
import { useState } from "react";
import { Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type TimeRange = {
  start: string;       // ISO UTC, e.g. "2026-05-15T08:00:00.000Z"
  end: string;         // ISO UTC
  label: string;       // human-readable label for the toolbar pill
};

type TimeRangePickerProps = {
  value: TimeRange;
  onChange: (range: TimeRange) => void;
};

const PRESETS: { label: string; minutes: number }[] = [
  { label: "Last 15 min", minutes: 15 },
  { label: "Last 1 h",    minutes: 60 },
  { label: "Last 6 h",    minutes: 360 },
  { label: "Last 24 h",   minutes: 1440 },
  { label: "Last 7 d",    minutes: 10080 },
  { label: "Last 30 d",   minutes: 43200 },
];

export function makePresetRange(minutes: number, label: string): TimeRange {
  const end = new Date();
  const start = new Date(end.getTime() - minutes * 60_000);
  return { start: start.toISOString(), end: end.toISOString(), label };
}

export default function TimeRangePicker({ value, onChange }: TimeRangePickerProps) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"preset" | "custom">("preset");
  // datetime-local inputs are timezone-naive; we store them as local strings
  // and convert to UTC when applying.
  const [customStart, setCustomStart] = useState(toLocalInput(value.start));
  const [customEnd, setCustomEnd]     = useState(toLocalInput(value.end));

  const applyPreset = (minutes: number, label: string) => {
    onChange(makePresetRange(minutes, label));
    setOpen(false);
  };

  const applyCustom = () => {
    if (!customStart || !customEnd) return;
    const startUtc = new Date(customStart).toISOString();
    const endUtc   = new Date(customEnd).toISOString();
    if (new Date(startUtc) >= new Date(endUtc)) {
      alert("Start must be before end.");
      return;
    }
    onChange({
      start: startUtc,
      end: endUtc,
      label: `${customStart.replace("T", " ")} → ${customEnd.replace("T", " ")}`,
    });
    setOpen(false);
  };

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5 text-xs h-8"
        onClick={() => setOpen((v) => !v)}
      >
        <Clock className="h-3.5 w-3.5" />
        {value.label}
        <span className="text-muted-foreground">▾</span>
      </Button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1 w-[320px] z-50
                     bg-card border border-border rounded-md shadow-lg"
        >
          {/* Tab strip */}
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
            <div className="p-2 grid grid-cols-2 gap-1">
              {PRESETS.map((p) => (
                <button
                  key={p.minutes}
                  type="button"
                  onClick={() => applyPreset(p.minutes, p.label)}
                  className="text-left px-3 py-2 rounded text-xs hover:bg-secondary/40"
                >
                  {p.label}
                </button>
              ))}
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

// Convert an ISO UTC timestamp to the format datetime-local inputs need
// (YYYY-MM-DDTHH:MM, local timezone, no Z).
function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
