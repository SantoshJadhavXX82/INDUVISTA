/**
 * Universal confirmation dialog (Phase 16.0d.1).
 *
 * DESIGN PRINCIPLE: any state-changing action in the app — toggle,
 * delete, write, acknowledge — must go through this dialog. Accidental
 * clicks should never mutate data. The dialog requires explicit user
 * intent: click the action button, then click Confirm in the modal.
 *
 * Severity levels control the Confirm button color:
 *   - destructive  → red (delete, remove, wipe)
 *   - warning      → amber (disable, override, force)
 *   - normal       → primary (enable, save, ack)
 *
 * Keyboard behavior:
 *   - Esc cancels
 *   - Backdrop click cancels
 *   - Cancel button is autoFocused (Enter cancels, doesn't accidentally confirm)
 *
 * For high-stakes destructive actions (e.g. delete a device with
 * downstream history), pass requireTextMatch — user must type a string
 * exactly to enable Confirm.
 *
 * Usage:
 *   const [pending, setPending] = useState<MyThing | null>(null);
 *
 *   <button onClick={() => setPending(thing)}>Delete</button>
 *
 *   <ConfirmDialog
 *     open={!!pending}
 *     title="Delete thing?"
 *     description={<>This will remove <b>{pending?.name}</b>.</>}
 *     confirmLabel="Delete"
 *     severity="destructive"
 *     busy={mutation.isPending}
 *     onConfirm={() => {
 *       mutation.mutate(pending!, { onSettled: () => setPending(null) });
 *     }}
 *     onCancel={() => setPending(null)}
 *   />
 */
import { useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, X } from "lucide-react";

type Severity = "destructive" | "warning" | "normal";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  severity?: Severity;
  busy?: boolean;
  /** If set, the Confirm button stays disabled until the user types
   *  this string verbatim. Use sparingly — only for truly destructive
   *  actions affecting other users' data or production state. */
  requireTextMatch?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

const SEVERITY_CONFIRM_CLS: Record<Severity, string> = {
  destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
  warning:     "bg-amber-600 text-white hover:bg-amber-700",
  normal:      "bg-primary text-primary-foreground hover:bg-primary/90",
};

const SEVERITY_ICON_CLS: Record<Severity, string> = {
  destructive: "text-destructive",
  warning:     "text-amber-600",
  normal:      "text-foreground",
};

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  severity = "normal",
  busy = false,
  requireTextMatch,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState("");

  // Reset typed-confirm state whenever the dialog reopens.
  useEffect(() => {
    if (!open) setTyped("");
  }, [open]);

  // Esc cancels (when not in-flight).
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        if (!busy) onCancel();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  const canConfirm = !busy && (!requireTextMatch || typed === requireTextMatch);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center
                 bg-black/50 backdrop-blur-sm"
      onClick={() => !busy && onCancel()}
      role="presentation"
    >
      <div
        className="bg-card border border-border rounded shadow-lg w-full max-w-md mx-4"
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="flex items-center gap-2">
            {severity !== "normal" && (
              <AlertTriangle className={`h-4 w-4 ${SEVERITY_ICON_CLS[severity]}`} />
            )}
            <h2 id="confirm-dialog-title" className="text-sm font-medium">
              {title}
            </h2>
          </div>
          <button
            type="button"
            onClick={() => !busy && onCancel()}
            disabled={busy}
            className="h-6 w-6 inline-flex items-center justify-center rounded
                       text-muted-foreground hover:bg-secondary disabled:opacity-30"
            aria-label="Close"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Body */}
        {description && (
          <div className="px-4 py-3 text-xs text-foreground/90 space-y-2 leading-relaxed">
            {description}
          </div>
        )}

        {/* Type-to-confirm (for high-stakes destructive actions) */}
        {requireTextMatch && (
          <div className="px-4 pb-3 -mt-1">
            <label className="text-[11px] text-muted-foreground mb-1 block">
              Type <code className="text-foreground font-mono bg-secondary/50 px-1 rounded">{requireTextMatch}</code> to confirm:
            </label>
            <input
              type="text"
              className="h-7 text-xs bg-card border border-border rounded px-2 w-full font-mono
                         focus:outline-none focus:ring-2 focus:ring-primary"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border bg-secondary/10">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            autoFocus
            className="text-xs px-3 py-1.5 rounded border border-border
                       hover:bg-secondary disabled:opacity-30 focus:outline-none
                       focus:ring-2 focus:ring-offset-1 focus:ring-primary"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!canConfirm}
            className={`text-xs px-3 py-1.5 rounded disabled:opacity-30 disabled:cursor-not-allowed ${SEVERITY_CONFIRM_CLS[severity]}`}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
