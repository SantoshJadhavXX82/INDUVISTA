/**
 * Phase 13.7 - Tooltip display mode selector.
 *
 * Full     - all 13 spec fields (timestamps, tag, description, value+EU,
 *            ST/quality, device/protocol/block/address/channel)
 * Compact  - just timestamp + per-tag value + quality chip; for dense
 *            chart inspection where the full card is too much
 * Off      - no tooltip at all
 *
 * Preference persists to localStorage so operators don't re-pick on
 * every page load.
 */
import { useEffect, useRef, useState } from "react";
import { MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";

export type TooltipMode = "full" | "compact" | "off";

const STORAGE_KEY = "induvista.tooltipMode";

const OPTIONS: { value: TooltipMode; label: string; hint: string }[] = [
  { value: "full",    label: "Full",    hint: "All fields per spec 8.3" },
  { value: "compact", label: "Compact", hint: "Time + per-tag value + quality" },
  { value: "off",     label: "Off",     hint: "Hide the tooltip entirely" },
];

export function loadTooltipMode(): TooltipMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "full" || v === "compact" || v === "off") return v;
  } catch { /* localStorage blocked */ }
  return "full";
}

export function saveTooltipMode(m: TooltipMode) {
  try { localStorage.setItem(STORAGE_KEY, m); } catch { /* ignore */ }
}

type Props = {
  value: TooltipMode;
  onChange: (v: TooltipMode) => void;
};

export default function TooltipModeSelector({ value, onChange }: Props) {
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
        onClick={() => setOpen((v) => !v)}
        title="Tooltip display mode"
      >
        <MessageSquare className="h-3 w-3" />
        Tooltip: {opt?.label ?? value}
      </Button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-[260px] z-50 bg-card border border-border rounded-md shadow-lg">
          <div className="p-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground px-1 pb-1">
              Tooltip mode
            </div>
            {OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => {
                  onChange(o.value);
                  saveTooltipMode(o.value);
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
