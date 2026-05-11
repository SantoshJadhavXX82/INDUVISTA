import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// shadcn/ui's standard cn() helper: merge conditional classes, dedupe Tailwind
// conflicts. Used by every shadcn component.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
