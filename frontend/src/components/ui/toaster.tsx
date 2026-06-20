/**
 * Toaster - renders active toasts from the toast store in a fixed
 * bottom-right stack. Mounted once in App. Auto-dismisses; click X to
 * close. Uses semantic surface tokens (bg-card / border / foreground)
 * with an iOS-palette accent bar so it matches the rest of the UI.
 */
import { useSyncExternalStore } from "react";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";
import {
  subscribe,
  getToasts,
  dismissToast,
  type ToastVariant,
} from "@/lib/toast";

const VARIANT: Record<ToastVariant, { color: string; Icon: typeof Info }> = {
  success: { color: "var(--ios-green, #34C759)", Icon: CheckCircle2 },
  error: { color: "var(--ios-red, #FF3B30)", Icon: AlertCircle },
  info: { color: "var(--ios-blue, #007AFF)", Icon: Info },
};

export function Toaster() {
  const toasts = useSyncExternalStore(subscribe, getToasts, getToasts);
  if (toasts.length === 0) return null;
  return (
    <div
      className="fixed bottom-4 right-4 z-[100] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2"
      aria-live="polite"
      aria-atomic="false"
    >
      {toasts.map((t) => {
        const v = VARIANT[t.variant];
        const Icon = v.Icon;
        return (
          <div
            key={t.id}
            role="status"
            className="flex items-start gap-2 rounded-lg border border-border bg-card px-3 py-2.5 text-sm text-foreground shadow-lg"
            style={{ borderLeftWidth: 4, borderLeftColor: v.color }}
          >
            <Icon className="mt-0.5 h-4 w-4 shrink-0" style={{ color: v.color }} />
            <span className="flex-1 leading-snug">{t.message}</span>
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={() => dismissToast(t.id)}
              className="shrink-0 text-muted-foreground transition-opacity hover:opacity-70"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
