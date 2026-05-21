import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router";
import App from "@/App";
import "@/index.css";

// Phase 18 — apply the saved theme SYNCHRONOUSLY before any React render.
// Without this, refreshing into dark mode would briefly show light theme
// while React mounts ("flash of wrong theme"). One localStorage read, one
// matchMedia check, one DOM attribute write — done in ~0.1ms.
(() => {
  try {
    const stored = window.localStorage.getItem("induvista:theme");
    const choice = stored === "light" || stored === "dark" || stored === "system"
      ? stored
      : "system";
    const resolved = choice === "system"
      ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : choice;
    document.documentElement.setAttribute("data-theme", resolved);
    document.documentElement.style.colorScheme = resolved;
  } catch { /* localStorage may be disabled — silently fall through to light */ }
})();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Diagnostics views auto-refresh; per-query refetchInterval handles that.
      // Default staleTime is short so other queries don't go stale on slow links.
      staleTime: 2_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
