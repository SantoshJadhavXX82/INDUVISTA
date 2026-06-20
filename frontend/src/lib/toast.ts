/**
 * Minimal dependency-free toast store. Import { toast } anywhere (even
 * outside React components) and call toast.success / error / info. The
 * <Toaster/> component (mounted once in App) renders the active toasts.
 */
export type ToastVariant = "success" | "error" | "info";
export type ToastItem = {
  id: number;
  message: string;
  variant: ToastVariant;
  duration: number;
};

let items: ToastItem[] = [];
const listeners = new Set<() => void>();
let nextId = 1;

function emit() {
  for (const l of listeners) l();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getToasts(): ToastItem[] {
  return items;
}

export function dismissToast(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

function push(message: string, variant: ToastVariant, duration: number): number {
  const id = nextId++;
  items = [...items, { id, message, variant, duration }];
  emit();
  if (duration > 0) {
    setTimeout(() => dismissToast(id), duration);
  }
  return id;
}

export const toast = {
  success: (message: string, duration = 4000) => push(message, "success", duration),
  error: (message: string, duration = 6000) => push(message, "error", duration),
  info: (message: string, duration = 4000) => push(message, "info", duration),
  dismiss: dismissToast,
};
