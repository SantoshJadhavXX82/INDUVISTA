/**
 * Phase 18 — Theme management.
 *
 * Three modes: "light" | "dark" | "system".
 *   - "light" / "dark" : explicit operator choice
 *   - "system"         : follow the OS preference (matchMedia)
 *
 * Persistence: localStorage key `induvista:theme`.
 *
 * The actual DOM mutation is one line — set `data-theme="light"` or
 * `data-theme="dark"` on <html>. The CSS in index.css already has a
 * `:where([data-theme="dark"])` block that overrides all the design
 * tokens. So flipping this attribute is enough to retheme the whole app.
 *
 * No-FOUC: the theme is also applied EARLY in main.tsx (before React
 * mounts) so users never see a flash of light theme on hard refresh
 * when they've chosen dark. This hook stays in sync after mount.
 */
import { useEffect, useState, useCallback } from "react";

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "induvista:theme";


/** Read the stored choice. Defaults to "system" for first-time users. */
export function readStoredTheme(): ThemeChoice {
  if (typeof window === "undefined") return "system";
  const v = window.localStorage.getItem(STORAGE_KEY);
  return v === "light" || v === "dark" || v === "system" ? v : "system";
}


/** What the OS says it prefers right now. */
export function readSystemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}


/** Apply a resolved theme to the document root. Idempotent. */
export function applyTheme(choice: ThemeChoice): ResolvedTheme {
  const resolved: ResolvedTheme = choice === "system"
    ? readSystemTheme()
    : choice;
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-theme", resolved);
    document.documentElement.style.colorScheme = resolved;
  }
  return resolved;
}


/**
 * Hook returning the current theme choice and resolved-mode plus a
 * setter. Re-applies on mount (in case main.tsx and React disagree
 * after navigation), persists to localStorage, and listens to OS
 * preference changes while in "system" mode.
 */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readStoredTheme);
  const [resolved, setResolved] = useState<ResolvedTheme>(() => applyTheme(readStoredTheme()));

  // Apply + persist whenever choice changes.
  useEffect(() => {
    const r = applyTheme(choice);
    setResolved(r);
    window.localStorage.setItem(STORAGE_KEY, choice);
  }, [choice]);

  // Listen for OS changes only while in "system" mode.
  useEffect(() => {
    if (choice !== "system" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => setResolved(applyTheme("system"));
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [choice]);

  const setTheme = useCallback((next: ThemeChoice) => setChoice(next), []);

  return { choice, resolved, setTheme };
}
