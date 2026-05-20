/**
 * Phase 11 — Tag quality indicator.
 *
 * Shows a colored dot + freshness for a tag based on its last sample.
 * Three states:
 *   • good   — quality byte is GOOD (>= 128) and recent (age < stale threshold)
 *   • stale  — quality byte is GOOD but age > stale threshold OR no sample yet
 *   • error  — quality byte is BAD (< 128) — comm failure, timeout, retry
 *              exhausted, etc.
 *
 * OPC quality-byte semantics (matches backend constants in
 * app/workers/calc_blocks/base.py):
 *   0 - 127  : BAD / UNCERTAIN (we treat as error)
 *   128 - 255: GOOD (with various sub-status codes)
 *       128 = GOOD_NON_SPECIFIC threshold (used by Modbus reads)
 *       192 = GOOD_LOCAL_OVERRIDE (used by calc evaluator outputs)
 *
 * The component is intentionally small (single-line text + dot) so it can
 * appear in a Tag Explorer table cell without breaking the row height.
 */
import { cn } from "@/lib/utils";

const ST_GOOD_MIN = 128;            // threshold: st >= 128 is GOOD
const STALE_AFTER_SEC = 30;         // values older than this are stale

export type TagQuality = "good" | "stale" | "error" | "unknown";

export function tagQuality(
  st: number | null,
  age_seconds: number | null,
): TagQuality {
  if (st === null || age_seconds === null) return "unknown";
  if (st < ST_GOOD_MIN) return "error";
  if (age_seconds > STALE_AFTER_SEC) return "stale";
  return "good";
}

export function formatAge(age_seconds: number | null): string {
  if (age_seconds === null) return "—";
  if (age_seconds < 1) return "<1s";
  if (age_seconds < 60) return `${Math.floor(age_seconds)}s`;
  if (age_seconds < 3600) return `${Math.floor(age_seconds / 60)}m`;
  if (age_seconds < 86400) return `${Math.floor(age_seconds / 3600)}h`;
  return `${Math.floor(age_seconds / 86400)}d`;
}

interface TagQualityBadgeProps {
  st: number | null;
  st_reason: string | null;
  age_seconds: number | null;
  className?: string;
}

export function TagQualityBadge({
  st, st_reason, age_seconds, className,
}: TagQualityBadgeProps) {
  const q = tagQuality(st, age_seconds);
  const age = formatAge(age_seconds);

  // Tooltip text — full state for forensic debugging without leaving the cell.
  const title =
    q === "unknown"
      ? "No sample recorded yet for this tag"
      : `ST ${st} (${st_reason ?? "—"}) · last sample ${age} ago`;

  const dot = (
    <span
      className={cn(
        "inline-block h-2 w-2 rounded-full",
        q === "good" && "bg-green-500",
        q === "stale" && "bg-amber-400",
        q === "error" && "bg-red-500",
        q === "unknown" && "bg-gray-300",
      )}
    />
  );

  // For "good" we hide the age unless > 5s (don't waste pixels for fresh data).
  const showAge = q !== "unknown" && (q !== "good" || (age_seconds ?? 0) > 5);

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs",
        q === "error" && "text-red-700",
        q === "stale" && "text-amber-700",
        q === "good" && "text-muted-foreground",
        q === "unknown" && "text-muted-foreground/60",
        className,
      )}
      title={title}
    >
      {dot}
      {showAge && <span className="tabular-nums">{age}</span>}
    </span>
  );
}
