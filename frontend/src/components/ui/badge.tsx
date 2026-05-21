import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * Phase 19 — Badge variants now use the iOS-style status tokens
 * (defined in index.css). The previous version used hardcoded Tailwind
 * colors (bg-emerald-100 text-emerald-700) which don't adapt to dark
 * mode — light tile + dark text became near-invisible on dark surfaces.
 * The CSS vars below shift automatically between light and dark.
 */
const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        success: "border-transparent",
        warning: "border-transparent",
        destructive: "border-transparent",
        outline: "text-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, style, ...props }: BadgeProps) {
  // Inline styles for the semantic variants so they consume the CSS
  // variables that flip in dark mode. Tailwind arbitrary values like
  // `bg-[var(--status-good-soft)]` work too but inline keeps the
  // component self-contained and easy to read.
  const variantStyle: React.CSSProperties = {};
  if (variant === "success") {
    variantStyle.backgroundColor = "var(--status-good-soft)";
    variantStyle.color = "var(--status-good-on-soft)";
  } else if (variant === "warning") {
    variantStyle.backgroundColor = "var(--status-warn-soft)";
    variantStyle.color = "var(--status-warn-on-soft)";
  } else if (variant === "destructive") {
    variantStyle.backgroundColor = "var(--status-error-soft)";
    variantStyle.color = "var(--status-error-on-soft)";
  }
  return (
    <div
      className={cn(badgeVariants({ variant }), className)}
      style={{ ...variantStyle, ...style }}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
