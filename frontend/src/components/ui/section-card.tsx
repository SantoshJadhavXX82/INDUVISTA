/**
 * Phase 18 — SectionCard
 *
 * iOS-style grouped container. Elevated surface floating on the grouped
 * background. The Title slot has its own row that stretches across the
 * full card width with optional right-aligned action chips.
 *
 * Used for: alarm lists, KPI groups, device tag groups, settings sections.
 *
 * Visual hierarchy:
 *   - Card has 14px corner radius (iOS signature)
 *   - 0.5px hairline border always — defined via --card-edge token
 *     (barely visible in light mode, more visible in dark mode where
 *     it's essential to distinguish the card from the grouped bg)
 *   - Subtle drop shadow that intensifies in dark mode
 *
 * Phase 19 — the `bordered` prop is preserved for backward compat but
 * the default styling now includes an edge. Use `bordered={false}` to
 * suppress (rare — only for nested grouped surfaces).
 *
 * Usage:
 *   <SectionCard title="Active alarms" action={<a>View all →</a>}>
 *     <AlarmRow ... />
 *     <AlarmRow ... />
 *   </SectionCard>
 *
 *   <SectionCard title="System resources" subtitle="DESKTOP-LBOXN1 / Windows">
 *     ...content
 *   </SectionCard>
 */
import { cn } from "@/lib/utils";

export interface SectionCardProps {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  /** Default true — the iOS edge token. Pass false for nested cards. */
  bordered?: boolean;
  className?: string;
  /** Removes the inner padding for cases where the children
   *  manage their own padding (e.g. tables, full-bleed lists). */
  flush?: boolean;
  children: React.ReactNode;
}

export function SectionCard({
  title, subtitle, action, bordered = true, className, flush = false, children,
}: SectionCardProps) {
  return (
    <section
      className={cn("overflow-hidden", className)}
      style={{
        backgroundColor: "var(--bg-elevated)",
        borderRadius: "var(--radius-lg-2)",
        border: bordered ? "0.5px solid var(--card-edge)" : "none",
        boxShadow: bordered ? "var(--card-shadow)" : "none",
      }}
    >
      {(title || action) && (
        <header
          className="flex items-baseline justify-between gap-3 px-4 pt-3 pb-2"
          style={{ borderBottom: "0.5px solid var(--separator)" }}
        >
          <div className="min-w-0">
            {title && (
              <h2 className="text-[13px] font-semibold leading-tight tracking-tight">
                {title}
              </h2>
            )}
            {subtitle && (
              <p
                className="text-[11px] mt-0.5 truncate"
                style={{ color: "var(--text-secondary)" }}
              >
                {subtitle}
              </p>
            )}
          </div>
          {action && (
            <div className="flex items-center gap-2 text-[12px] shrink-0">
              {action}
            </div>
          )}
        </header>
      )}
      <div className={flush ? "" : "px-4 py-3"}>
        {children}
      </div>
    </section>
  );
}
