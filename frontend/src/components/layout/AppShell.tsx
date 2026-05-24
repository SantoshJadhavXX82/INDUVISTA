import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { type HealthResponse } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import Nav from "@/components/layout/Nav";
import MobileTabBar from "@/components/layout/MobileTabBar";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { useIsMobile } from "@/lib/use-media-query";

export default function AppShell({ children }: { children: React.ReactNode }) {
  // Light heartbeat at the app shell — keeps the role/version visible and
  // tells the user whether the API is reachable at all. /health is not under
  // /api, so we bypass the api wrapper here.
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const res = await fetch("/health");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as HealthResponse;
    },
    refetchInterval: 10_000,
    retry: false,
  });

  // Phase 19 — responsive: sidebar on desktop, bottom tab bar on mobile.
  const isMobile = useIsMobile();

  return (
    <div className="flex h-screen" style={{ backgroundColor: "var(--bg-grouped)", color: "var(--ios-gray-1)" }}>
      {!isMobile && (
      <aside
        className="w-60 shrink-0 flex flex-col"
        style={{
          backgroundColor: "var(--bg-elevated)",
          borderRight: "0.5px solid var(--separator)",
        }}
      >
        <div className="px-4 py-4" style={{ borderBottom: "0.5px solid var(--separator)" }}>
          <div className="flex items-center gap-2.5">
            {/* iOS-style brand mark: iOS-blue rounded square with a white
                monitoring-pulse glyph. Inline SVG so it scales perfectly
                and tints via CSS vars (will auto-adapt when dark mode is
                turned on). Replaces the dark photographic /icon_induvista.png
                which had no contrast against the white sidebar. */}
            <div
              className="shrink-0 flex items-center justify-center"
              style={{
                width: 38,
                height: 38,
                backgroundColor: "var(--ios-blue)",
                borderRadius: 9,    // ≈24% radius — iOS app icon proportion
                boxShadow: "0 1px 2px rgba(0, 0, 0, 0.08)",
              }}
              aria-label="InduVista"
              role="img"
            >
              <Activity
                className="text-white"
                style={{ width: 22, height: 22 }}
                strokeWidth={2.5}
              />
            </div>
            <div>
              <div
                className="text-lg font-semibold tracking-tight leading-tight"
                style={{ color: "var(--text-primary)", letterSpacing: "-0.02em" }}
              >InduVista</div>
              <div
                className="text-[10px] uppercase tracking-wider mt-0.5"
                style={{ color: "var(--text-secondary)" }}
              >
                Industrial Reporting Tool
              </div>
            </div>
          </div>
        </div>
        <Nav />
      </aside>
      )}

      <div className="flex-1 flex flex-col overflow-hidden">
        <header
          className="h-12 flex items-center justify-end gap-3 px-4"
          style={{
            backgroundColor: "var(--bg-elevated)",
            borderBottom: "0.5px solid var(--separator)",
          }}
        >
          <ThemeToggle />
          {health.isError ? (
            <Badge variant="destructive">backend unreachable</Badge>
          ) : health.data ? (
            <>
              <Badge variant="outline" className="font-mono text-xs">
                {health.data.app_env}
              </Badge>
              <Badge
                variant={health.data.role === "active" ? "success" : "warning"}
                className="font-mono text-xs"
              >
                role: {health.data.role}
              </Badge>
              <span
                className="text-xs tabular-nums"
                style={{ color: "var(--text-secondary)" }}
                title={
                  `Backend uptime: ${formatUptime(health.data.uptime_sec)}\n` +
                  `Started: ${health.data.started_at}\n` +
                  `Cycle count: ${health.data.cycle_count}\n` +
                  `Migration: ${health.data.migration_version ?? "—"}`
                }
              >
                db {health.data.db_latency_ms.toFixed(1)} ms
              </span>
            </>
          ) : (
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>connecting…</span>
          )}
        </header>

        <main
          className="flex-1 overflow-auto"
          style={{
            color: "var(--text-primary)",
            padding: isMobile ? "12px 12px 80px" : "24px",   // bottom padding leaves room for the tab bar
          }}
        >{children}</main>
      </div>
      {isMobile && <MobileTabBar />}
    </div>
  );
}

function formatUptime(sec: number): string {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
