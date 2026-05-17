/**
 * Phase 14.8 — Severity rendering primitives.
 *
 * Replaces the hardcoded switch statements scattered across the alarms
 * UI (severityBadgeClass, severityBarClass, severityRowBgClass) with
 * helpers that read the live alarm_severities master list (and its
 * color_hex column). Custom severities defined under
 * Setup > Alarm Severities now use their configured colors throughout
 * the alarms surface.
 *
 * Three primitives:
 *   - <SeverityBadge code={...} />     — outlined badge with label
 *   - useSeverityColors(code)          — { color, label } hook for
 *                                        callers that need raw values
 *                                        (left bars, row bg tints,
 *                                        icon strokes, etc.)
 *   - hexWithAlpha(hex, alpha)         — pure helper for tinting
 *
 * All callsites should consume these. The previous switch-on-code
 * helpers are removed in this phase.
 */

import { Badge } from "@/components/ui/badge";
import { useSeverities } from "@/lib/useSeverities";


// Slate-500 (#64748b). Used when the severities list isn't loaded yet
// or a rule references a code that was deleted. Stays neutral so the
// UI never goes blank.
const FALLBACK_HEX = "#64748b";


/** Append a 0-1 alpha to a 7-char "#rrggbb" hex. Returns the input
 * unchanged if it isn't a valid hex (defensive — color_hex IS regex-
 * validated server-side, but a future schema migration could weaken
 * that and we'd rather render than throw). */
export function hexWithAlpha(hex: string, alpha: number): string {
  if (!hex.startsWith("#") || hex.length !== 7) return hex;
  const clamped = Math.max(0, Math.min(1, alpha));
  const a = Math.round(clamped * 255).toString(16).padStart(2, "0");
  return hex + a;
}


/** Look up { color, label, rank } for a severity code. Returns
 * the slate fallback when the code isn't found. */
export function useSeverityColors(code: string): {
  color: string;
  label: string;
  rank: number;
} {
  const { data } = useSeverities();
  const sev = (data ?? []).find((s) => s.code === code);
  return {
    color: sev?.color_hex ?? FALLBACK_HEX,
    label: sev?.label ?? code,
    rank: sev?.rank ?? 999,
  };
}


interface SeverityBadgeProps {
  code: string;
  /** Extra className applied to the badge (text size, font weight). */
  className?: string;
}

/**
 * Outlined badge tinted with the severity's color_hex.
 *
 *   bg     = color_hex at 10% opacity   (soft fill)
 *   border = color_hex at full opacity  (definite outline)
 *   text   = color_hex at full opacity  (matches outline)
 *
 * Defaults to the "outline" Badge variant + smaller text. Callers can
 * override sizing via className.
 */
export function SeverityBadge({
  code,
  className = "text-[10px]",
}: SeverityBadgeProps) {
  const { color, label } = useSeverityColors(code);
  return (
    <Badge
      variant="outline"
      className={className}
      style={{
        backgroundColor: hexWithAlpha(color, 0.10),
        borderColor: color,
        color: color,
      }}
    >
      {label}
    </Badge>
  );
}
