/**
 * Phase 19 — useMediaQuery / useIsMobile.
 *
 * React hook for responsive design. Watches a CSS media query and
 * returns its current match state, updating on resize. Used to switch
 * between desktop (sidebar + multi-column) and mobile (swipe-pages +
 * bottom tabs) layouts.
 *
 * Breakpoint: 768px. Tailwind's `md` breakpoint, also the iPad-mini
 * portrait width. Below this we go mobile. Tablet in landscape and up
 * stays desktop.
 */
import { useEffect, useState } from "react";


/** Returns true when the media query currently matches. SSR-safe. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    mq.addEventListener("change", handler);
    setMatches(mq.matches);   // sync in case it changed between render and effect
    return () => mq.removeEventListener("change", handler);
  }, [query]);

  return matches;
}


/** Convenience hook — true on phones and small tablets in portrait. */
export function useIsMobile(): boolean {
  return useMediaQuery("(max-width: 767px)");
}
