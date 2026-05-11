import * as React from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Side-drawer overlay. Used by Tag Explorer (detail/edit panel) and the
 * config editor (create/edit forms). Hand-rolled instead of pulling in a
 * Radix Sheet primitive — keeps dependency surface small.
 *
 * Closes on Escape, on backdrop click, and via the X button.
 */
type DrawerProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  /** width of the panel; defaults to ~28rem */
  size?: "default" | "lg";
};

export function Drawer({ open, onClose, title, children, size = "default" }: DrawerProps) {
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", handler);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/40" onClick={onClose} aria-label="Close drawer" />
      <div
        className={cn(
          "bg-background shadow-2xl flex flex-col border-l",
          size === "lg" ? "w-full max-w-2xl" : "w-full max-w-md",
        )}
      >
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-4">{children}</div>
      </div>
    </div>
  );
}
