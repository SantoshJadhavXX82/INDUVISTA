/**
 * Phase 13.10 - Quality filter selector for the trend toolbar (spec 9.4).
 *
 * Filters which samples render on the chart based on their quality class:
 *   - all       : show every sample regardless of ST
 *   - hide_bad  : drop samples with ST < 64 (bad)
 *   - good_only : drop everything below ST 128 (only good)
 *
 * The bad/uncertain marker overlays follow the filter - in good_only mode
 * the bad and uncertain marker series are hidden too, so the chart shows
 * an honest "this is what's known-good" view.
 *
 * Preference persists to localStorage so operators don't re-pick on every
 * page load.
 */
import { useEffect, useRef, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";

export type QualityFilter = "all" | "hide_bad" | "good_only";

const STORAGE_KEY = "induvista.qualityFilter";

const OPTIONS: { value: QualityFilter; label: string; hint: string }[] = [
  { value: "all",       label: "Show all",  hint: "Every sample, including bad" },
  { value: "hide_bad",  label: "Hide bad",  hint: "Drop ST < 64 (bad readings)" },
  { value: "good_only", label: "Good only", hint: "Only ST >= 128 (good readings)" },
];

export function loadQualityFilter(): QualityFilter {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "all" || v === "hide_bad" || v === "good_only") return v;
  } catch { /* localStorage blocked */ }
  return "all";
}

export function saveQualityFilter(f: QualityFilter) {
  try { localStorage.setItem(STORAGE_KEY, f); } catch { /* ignore */ }
}

type Props = {
  value: QualityFilter;
  onChange: (v: QualityFilter) => void;
};

export default function QualityFilterSelector({ value, onChange }: Props) {
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
        className={`h-8 text-xs gap-1.5 ${value !== "all" ? "border-blue-400 text-blue-700" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Quality filter (spec 9.4)"
      >
        <ShieldCheck className="h-3 w-3" />
        Quality: {opt?.label ?? value}
      </Button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-[280px] z-50 bg-card border border-border rounded-md shadow-lg">
          <div className="p-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pb-1">
              Quality filter
            </div>
            {OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => {
                  onChange(o.value);
                  saveQualityFilter(o.value);
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
