/**
 * Phase 18 — StatusPill
 *
 * Soft-tinted iOS-style badge for severity and status. The "soft" treatment
 * (light tint background + saturated text) is the iOS convention — bolder
 * solid badges feel heavy against the rest of the app.
 *
 * Semantic variants map to CSS variables defined in index.css, so a future
 * dark mode toggle changes every pill across the app automatically. The
 * severity variants (vcritical / critical / high / medium / low / info /
 * noncritical) align with the existing rows in your alarm_severities table
 * so the same data drives the right color.
 *
 * Usage:
 *   <StatusPill variant="error">vCritical</StatusPill>
 *   <StatusPill variant="info" size="sm">INFO</StatusPill>
 *   <StatusPill severity="high">High</StatusPill>
 *   <StatusPill variant="good" dot>Connected</StatusPill>
 */
import { cn } from "@/lib/utils";

type StatusVariant = "good" | "warn" | "error" | "info" | "neutral";
type SeverityVariant =
  | "vcritical" | "critical" | "high" | "medium"
  | "low" | "info" | "noncritical";

export interface StatusPillProps {
  variant?: StatusVariant;
  severity?: SeverityVariant;
  size?: "sm" | "md";
  /** When true, prepends a small colored dot. */
  dot?: boolean;
  children: React.ReactNode;
  className?: string;
}

const VARIANT_STYLES: Record<StatusVariant, { bg: string; fg: string; dot: string }> = {
  good:    { bg: "var(--status-good-soft)",    fg: "var(--status-good-on-soft)",    dot: "var(--status-good)" },
  warn:    { bg: "var(--status-warn-soft)",    fg: "var(--status-warn-on-soft)",    dot: "var(--status-warn)" },
  error:   { bg: "var(--status-error-soft)",   fg: "var(--status-error-on-soft)",   dot: "var(--status-error)" },
  info:    { bg: "var(--status-info-soft)",    fg: "var(--status-info-on-soft)",    dot: "var(--status-info)" },
  neutral: { bg: "var(--status-neutral-soft)", fg: "var(--status-neutral-on-soft)", dot: "var(--status-neutral)" },
};

const SEVERITY_STYLES: Record<SeverityVariant, { bg: string; fg: string; dot: string }> = {
  vcritical:    { bg: "var(--severity-vcritical-bg)",    fg: "var(--severity-vcritical-fg)",    dot: "var(--severity-vcritical)" },
  critical:     { bg: "var(--severity-critical-bg)",     fg: "var(--severity-critical-fg)",     dot: "var(--severity-critical)" },
  high:         { bg: "var(--severity-high-bg)",         fg: "var(--severity-high-fg)",         dot: "var(--severity-high)" },
  medium:       { bg: "var(--severity-medium-bg)",       fg: "var(--severity-medium-fg)",       dot: "var(--severity-medium)" },
  low:          { bg: "var(--severity-low-bg)",          fg: "var(--severity-low-fg)",          dot: "var(--severity-low)" },
  info:         { bg: "var(--severity-info-bg)",         fg: "var(--severity-info-fg)",         dot: "var(--severity-info)" },
  noncritical:  { bg: "var(--severity-noncritical-bg)",  fg: "var(--severity-noncritical-fg)",  dot: "var(--severity-noncritical)" },
};

export function StatusPill({
  variant, severity, size = "md", dot = false, children, className,
}: StatusPillProps) {
  const style = severity
    ? SEVERITY_STYLES[severity]
    : VARIANT_STYLES[variant ?? "neutral"];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-medium whitespace-nowrap",
        size === "sm"
          ? "text-[10px] px-2 py-0.5 leading-tight"
          : "text-[11px] px-2.5 py-1 leading-tight",
        "rounded-md",
        className,
      )}
      style={{
        backgroundColor: style.bg,
        color: style.fg,
        borderRadius: "var(--radius-sm-2)",
      }}
    >
      {dot && (
        <span
          aria-hidden="true"
          className="inline-block rounded-full"
          style={{
            width: size === "sm" ? 5 : 6,
            height: size === "sm" ? 5 : 6,
            backgroundColor: style.dot,
          }}
        />
      )}
      {children}
    </span>
  );
}
