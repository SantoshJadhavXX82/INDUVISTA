/**
 * Skeleton - shimmer placeholder for loading states. Uses the semantic
 * --muted token so it adapts to both light and dark themes. Pure CSS via
 * animate-pulse (a core Tailwind utility) - no dependency.
 */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-md bg-muted ${className}`}
      aria-hidden="true"
    />
  );
}

/**
 * PageSkeleton - generic full-page loading placeholder used as the lazy
 * route Suspense fallback. Approximates a typical page (title, subtitle,
 * summary cards, a table) so route transitions feel intentional instead
 * of flashing bare text.
 */
export function PageSkeleton() {
  return (
    <div className="space-y-4 p-6" role="status" aria-label="Loading page">
      <Skeleton className="h-7 w-56" />
      <Skeleton className="h-4 w-80" />
      <div className="grid grid-cols-1 gap-4 pt-2 sm:grid-cols-3">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
      <div className="space-y-2 pt-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-11 w-full" />
        ))}
      </div>
    </div>
  );
}
