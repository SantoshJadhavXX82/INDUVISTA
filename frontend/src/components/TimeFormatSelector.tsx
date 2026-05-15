/**
 * Phase 13.6 - Time format toggle for the trend toolbar.
 *
 * Compact dropdown: 24-hour / 12-hour / System auto. Reads and writes
 * the global TimeFormatProvider's mode.
 */
import { useEffect, useRef, useState } from "react";
import { Settings } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTimeFormat, type TimeFormatMode } from "@/lib/timeFormat";

const OPTIONS: { value: TimeFormatMode; label: string; hint: string }[] = [
  { value: "24h",  label: "24-hour",       hint: "HH:MM (industrial default)" },
  { value: "12h",  label: "12-hour",       hint: "h:MM AM/PM" },
  { value: "auto", label: "System default", hint: "Follow browser locale" },
];

export default function TimeFormatSelector() {
  const { mode, setMode, is24h } = useTimeFormat();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Show the resolved format in the button label so the user always
  // sees what's actually applied (especially helpful in Auto mode).
  const resolvedLabel = is24h ? "24h" : "12h";
  const modeLabel = mode === "auto" ? `Auto - ${resolvedLabel}` : resolvedLabel;

  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => setOpen((v) => !v)}
        title="Time display format"
      >
        <Settings className="h-3 w-3" />
        {modeLabel}
      </Button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-[240px] z-50 bg-card border border-border rounded-md shadow-lg">
          <div className="p-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pb-1">
              Time format
            </div>
            {OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => { setMode(o.value); setOpen(false); }}
                className={`w-full text-left px-3 py-2 rounded text-xs ${mode === o.value ? "bg-secondary font-medium" : "hover:bg-secondary/40"}`}
              >
                <div className="flex items-center justify-between">
                  <span>{o.label}</span>
                  {mode === o.value && <span className="text-[10px] text-emerald-700">SELECTED</span>}
                </div>
                <div className="text-[10px] text-muted-foreground mt-0.5">{o.hint}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
