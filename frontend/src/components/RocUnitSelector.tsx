/**
 * Phase 13.12 — Segmented toggle for the rate-of-change display unit.
 *
 * Stateless; parent owns the selection and persistence (via the
 * loadRocUnit/saveRocUnit helpers in lib/trendRoc).
 */
import { ROC_UNITS, type RocUnit } from "@/lib/trendRoc";

interface Props {
  value: RocUnit;
  onChange: (u: RocUnit) => void;
  /** Optional extra classes (margin, alignment) applied to the outer wrapper. */
  className?: string;
}

export default function RocUnitSelector({
  value,
  onChange,
  className = "",
}: Props) {
  return (
    <div
      className={`inline-flex items-center gap-1.5 text-xs ${className}`.trim()}
    >
      <span className="text-slate-500">ROC:</span>
      <div
        className="inline-flex rounded-md border border-slate-300 overflow-hidden"
        role="group"
        aria-label="Rate-of-change unit"
      >
        {ROC_UNITS.map((u, i) => {
          const active = value === u.value;
          return (
            <button
              key={u.value}
              type="button"
              onClick={() => onChange(u.value)}
              aria-pressed={active}
              data-roc-unit={u.value}
              className={
                "px-2 py-0.5 text-xs font-medium transition-colors " +
                (active
                  ? "bg-teal-600 text-white"
                  : "bg-white text-slate-700 hover:bg-slate-100") +
                (i > 0 ? " border-l border-slate-300" : "")
              }
            >
              {u.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
