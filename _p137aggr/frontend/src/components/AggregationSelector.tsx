/**
 * Phase 13.7 - Aggregation interval selector for the trend toolbar.
 *
 * Spec section 16.3 — Aggregation Intervals. Backend currently has CAs
 * at 1m/1h/1d so we expose those plus Raw (no aggregation) plus Auto
 * (lets backend route based on window width). Future phases can add
 * 5s/10s/30s/5m/15m once more granular CAs land.
 */
import { useEffect, useRef, useState } from "react";
import { Layers } from "lucide-react";
import { Button } from "@/components/ui/button";

export type AggregationOption = "auto" | "raw" | "1m" | "1h" | "1d";

const OPTIONS: { value: AggregationOption; label: string; hint: string }[] = [
  { value: "auto", label: "Auto",  hint: "Routed by window size (recommended)" },
  { value: "raw",  label: "Raw",   hint: "Every individual sample" },
  { value: "1m",   label: "1 min", hint: "1-minute buckets" },
  { value: "1h",   label: "1 hour", hint: "1-hour buckets" },
  { value: "1d",   label: "1 day", hint: "1-day buckets" },
];

type Props = {
  value: AggregationOption;
  onChange: (v: AggregationOption) => void;
  effective?: string;  // what the backend actually used (when Auto)
};

export default function AggregationSelector({ value, onChange, effective }: Props) {
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
  // Show effective when Auto so operators see what's actually rendered.
  const label = value === "auto" && effective && effective !== "auto"
    ? `Auto: ${effective}`
    : (opt?.label ?? value);

  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => setOpen((v) => !v)}
        title="Aggregation interval"
      >
        <Layers className="h-3 w-3" />
        {label}
      </Button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-[260px] z-50 bg-card border border-border rounded-md shadow-lg">
          <div className="p-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pb-1">
              Aggregation
            </div>
            {OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => { onChange(o.value); setOpen(false); }}
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
