import { useQuery } from "@tanstack/react-query";
import { type HealthResponse } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import Nav from "@/components/layout/Nav";
import TimeFormatSelector from "@/components/TimeFormatSelector";

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

  return (
    <div className="flex h-screen bg-background text-foreground">
      <aside className="w-60 shrink-0 border-r flex flex-col">
        <div className="px-4 py-4 border-b">
          <div className="flex items-center gap-2.5">
            <img
              src="/icon_induvista.png"
              alt="InduVista"
              className="h-9 w-9 rounded shrink-0 object-contain"
            />
            <div>
              <div className="text-lg font-bold tracking-tight leading-tight">InduVista</div>
              <div className="text-[10px] text-muted-foreground uppercase tracking-wider mt-0.5">
                Industrial Reporting Tool
              </div>
            </div>
          </div>
        </div>
        <Nav />
      </aside>

      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="h-12 border-b flex items-center justify-end gap-3 px-4">
          {/* Global time format - applies app-wide (chart labels, summary
              tooltips, future timestamp columns in any module). */}
          <TimeFormatSelector />
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
                className="text-xs text-muted-foreground tabular-nums"
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
            <span className="text-xs text-muted-foreground">connecting…</span>
          )}
        </header>

        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
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
