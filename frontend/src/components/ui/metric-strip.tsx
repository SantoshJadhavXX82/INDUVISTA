/**
 * Phase 18 — MetricStrip
 *
 * Horizontal strip of small status cards. Used at the top of the dashboard
 * to give an at-a-glance system overview (workers, alarms, samples/s, etc).
 *
 * Each card has:
 *   - Colored dot indicating status tier
 *   - Tiny uppercase label
 *   - Big primary value
 *   - Optional secondary text below
 *
 * iOS design choice: NO left-border accents. Status communicated through
 * the leading colored dot beside the label. Keeps surfaces calm and
 * surfaces don't fight each other for visual weight.
 *
 * Usage:
 *   <MetricStrip
 *     items={[
 *       { label: "Workers", value: "5/5", tone: "good", hint: "All healthy" },
 *       { label: "Alarms",  value: 3,     tone: "error", hint: "1 crit · 2 hi" },
 *       { label: "Samples/s", value: 247, tone: "info" },
 *       { label: "DB latency", value: "4 ms", tone: "good" },
 *     ]}
 *   />
 */
import { cn } from "@/lib/utils";

export type MetricTone = "good" | "warn" | "error" | "info" | "neutral";

const TONE_DOT: Record<MetricTone, string> = {
  good:    "var(--status-good)",
  warn:    "var(--status-warn)",
  error:   "var(--status-error)",
  info:    "var(--status-info)",
  neutral: "var(--status-neutral)",
};

const TONE_HINT: Record<MetricTone, string> = {
  good:    "var(--status-good-on-soft)",
  warn:    "var(--status-warn-on-soft)",
  error:   "var(--status-error-on-soft)",
  info:    "var(--status-info-on-soft)",
  neutral: "var(--status-neutral-on-soft)",
};

export interface MetricItem {
  label: string;
  value: React.ReactNode;
  tone?: MetricTone;
  hint?: string;
  onClick?: () => void;
}

export interface MetricStripProps {
  items: MetricItem[];
  className?: string;
}

export function MetricStrip({ items, className }: MetricStripProps) {
  return (
    <div
      className={cn("grid gap-2", className)}
      style={{
        gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))`,
      }}
    >
      {items.map((item, i) => (
        <MetricCard key={i} item={item} />
      ))}
    </div>
  );
}

function MetricCard({ item }: { item: MetricItem }) {
  const tone = item.tone ?? "neutral";
  const interactive = !!item.onClick;
  return (
    <div
      className={cn(
        "min-w-0",
        interactive && "cursor-pointer transition-transform hover:scale-[1.01] active:scale-[0.99]",
      )}
      style={{
        backgroundColor: "var(--bg-elevated)",
        borderRadius: "var(--radius-lg-2)",
        padding: "10px 14px",
      }}
      onClick={item.onClick}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
    >
      <div
        className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider truncate"
        style={{ color: "var(--ios-gray-1)" }}
      >
        <span
          aria-hidden="true"
          className="inline-block rounded-full shrink-0"
          style={{ width: 7, height: 7, backgroundColor: TONE_DOT[tone] }}
        />
        {item.label}
      </div>
      <div
        className="font-semibold tabular-nums leading-tight mt-1"
        style={{
          fontSize: 22,
          letterSpacing: "-0.02em",
        }}
      >
        {item.value}
      </div>
      {item.hint && (
        <div
          className="text-[11px] mt-0.5 truncate"
          style={{ color: TONE_HINT[tone] }}
        >
          {item.hint}
        </div>
      )}
    </div>
  );
}
