/**
 * Phase 13.11 - Aggregation MODE selector (spec 16.1).
 *
 * Distinct from AggregationSelector (which picks the bucket INTERVAL):
 *   - INTERVAL = 1m / 1h / 1d (or auto, or raw)         <- bucket width
 *   - MODE     = last / first / avg / min / max         <- what represents the bucket
 *
 * When mode is "last" the chart shows the most-recent sample within each
 * bucket (default, matches a real-time recorder). "Avg" smooths short
 * spikes. "Min" / "Max" surface bucket extremes useful for spotting peaks
 * or troughs that "avg" would otherwise hide.
 *
 * Disabled when the effective interval is raw (each point is itself, no
 * summarization to choose).
 */
import { useEffect, useRef, useState } from "react";
import { Sigma } from "lucide-react";
import { Button } from "@/components/ui/button";

export type AggregationMode = "last" | "first" | "avg" | "min" | "max";

const STORAGE_KEY = "induvista.aggregationMode";

const OPTIONS: { value: AggregationMode; label: string; hint: string }[] = [
  { value: "last",  label: "Last",    hint: "Last sample in each bucket (default)" },
  { value: "first", label: "First",   hint: "First sample in each bucket" },
  { value: "avg",   label: "Average", hint: "Bucket mean - smooths short spikes" },
  { value: "min",   label: "Min",     hint: "Bucket trough - surfaces lows" },
  { value: "max",   label: "Max",     hint: "Bucket peak - surfaces highs" },
];

export function loadAggregationMode(): AggregationMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "last" || v === "first" || v === "avg" || v === "min" || v === "max") {
      return v;
    }
  } catch { /* localStorage blocked */ }
  return "last";
}

export function saveAggregationMode(m: AggregationMode) {
  try { localStorage.setItem(STORAGE_KEY, m); } catch { /* ignore */ }
}

type Props = {
  value: AggregationMode;
  onChange: (v: AggregationMode) => void;
  /** True when the active aggregation is "raw" - mode has no meaning then,
   *  so the selector renders disabled with an explanatory tooltip. */
  disabled?: boolean;
};

export default function AggregationModeSelector({ value, onChange, disabled }: Props) {
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

  const opt = OPTIONS.find((o) => o.value === value);

  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => disabled ? null : setOpen((v) => !v)}
        disabled={disabled}
        title={disabled
          ? "Mode applies only to aggregated views (current view is raw)"
          : "Aggregation mode - how each bucket is summarized"}
      >
        <Sigma className="h-3 w-3" />
        Mode: {opt?.label ?? value}
      </Button>
      {open && !disabled && (
        <div className="absolute right-0 top-full mt-1 w-[280px] z-50 bg-card border border-border rounded-md shadow-lg">
          <div className="p-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pb-1">
              Aggregation mode
            </div>
            {OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => {
                  onChange(o.value);
                  saveAggregationMode(o.value);
                  setOpen(false);
                }}
                className={`w-full text-left px-3 py-2 rounded text-xs ${value === o.value ? "bg-secondary font-medium" : "hover:bg-secondary/40"}`}
              >
                <div className="flex items-center justify-between">
                  <span>{o.label}</span>
                  {value === o.value && <span className="text-[10px] text-emerald-700">SELECTED</span>}
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
