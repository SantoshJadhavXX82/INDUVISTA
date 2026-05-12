/**
 * HelpTip — small ⓘ marker next to a label that opens a tooltip with
 * structured help-text content. Accessible by hover, click/tap, and
 * keyboard focus (Tab + Enter/Space).
 *
 * Phase 8.4 — the foundation. Drop one next to any Label:
 *
 *   <Label>
 *     Engineering unit
 *     <HelpTip entry={help.tag.engineering_unit} />
 *   </Label>
 *
 * The popover anchors below-and-left of the icon and auto-flips above if
 * there isn't room below.
 */
import { useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";

import { cn } from "@/lib/utils";
import type { HelpEntry } from "@/lib/help-text";

export function HelpTip({
  entry,
  className,
}: {
  entry: HelpEntry;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<"bottom" | "top">("bottom");
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape
  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (
        buttonRef.current && !buttonRef.current.contains(e.target as Node) &&
        popoverRef.current && !popoverRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Decide whether to flip above when there's no room below
  useEffect(() => {
    if (!open || !buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    setPosition(spaceBelow < 180 ? "top" : "bottom");
  }, [open]);

  return (
    <span className={cn("relative inline-flex items-center", className)}>
      <button
        ref={buttonRef}
        type="button"
        onClick={(e) => {
          e.preventDefault();
          setOpen((v) => !v);
        }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={(e) => {
          // Don't close immediately if hovering over the popover
          if (popoverRef.current && !popoverRef.current.contains(e.relatedTarget as Node)) {
            setTimeout(() => {
              if (!popoverRef.current?.matches(":hover")) setOpen(false);
            }, 100);
          }
        }}
        onFocus={() => setOpen(true)}
        onBlur={(e) => {
          // Keep open if focus moved to within the popover
          if (popoverRef.current && popoverRef.current.contains(e.relatedTarget as Node)) {
            return;
          }
          setOpen(false);
        }}
        className="ml-1 text-muted-foreground hover:text-foreground focus:text-foreground focus:outline-none focus:ring-2 focus:ring-ring rounded-full"
        aria-label="More info"
        aria-expanded={open}
      >
        <Info className="h-3.5 w-3.5" />
      </button>

      {open && (
        <div
          ref={popoverRef}
          role="tooltip"
          className={cn(
            "absolute z-50 w-72 rounded-md border bg-popover shadow-md p-3 text-sm",
            "left-0",
            position === "bottom" ? "top-full mt-1" : "bottom-full mb-1",
          )}
          onMouseLeave={() => setOpen(false)}
        >
          <p className="text-foreground leading-snug">{entry.description}</p>
          {entry.example && (
            <p className="mt-2 text-xs text-muted-foreground">
              <span className="font-semibold uppercase tracking-wide text-[10px]">Example:</span>{" "}
              {entry.example}
            </p>
          )}
          {entry.impact && (
            <p className="mt-2 text-xs text-muted-foreground">
              <span className="font-semibold uppercase tracking-wide text-[10px]">Impact:</span>{" "}
              {entry.impact}
            </p>
          )}
        </div>
      )}
    </span>
  );
}
