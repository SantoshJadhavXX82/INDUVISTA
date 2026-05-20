/**
 * Phase 18 — PageHeader
 *
 * Standard page-level header with title, optional subtitle (metadata
 * like "335 tags · refreshed 2s ago"), and a right-aligned actions slot.
 *
 * Replaces the inconsistent "<h1>Page name</h1>" patterns scattered
 * across the existing pages. Every page should use this for the top row.
 *
 * Usage:
 *   <PageHeader
 *     title="Dashboard"
 *     subtitle="335 tags · refreshed 2s ago"
 *     actions={
 *       <>
 *         <StatusPill variant="good" dot>All healthy</StatusPill>
 *         <Button size="sm" variant="ghost">Refresh</Button>
 *       </>
 *     }
 *   />
 */
import { cn } from "@/lib/utils";

export interface PageHeaderProps {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}

export function PageHeader({
  title, subtitle, actions, className,
}: PageHeaderProps) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 mb-4",
        className,
      )}
    >
      <div className="min-w-0">
        <h1
          className="text-[22px] font-semibold leading-tight tracking-tight"
          style={{ letterSpacing: "-0.02em" }}
        >
          {title}
        </h1>
        {subtitle && (
          <p
            className="text-[12px] mt-0.5 truncate"
            style={{ color: "var(--ios-gray-1)" }}
          >
            {subtitle}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex items-center gap-2 shrink-0">
          {actions}
        </div>
      )}
    </div>
  );
}
