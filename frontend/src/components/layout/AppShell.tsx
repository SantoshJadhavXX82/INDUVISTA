import { useQuery } from "@tanstack/react-query";
import { type HealthResponse } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import Nav from "@/components/layout/Nav";

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
          <div className="text-lg font-bold tracking-tight">InduVista</div>
          <div className="text-xs text-muted-foreground mt-0.5">
            Industrial Reporting Tool
          </div>
        </div>
        <Nav />
      </aside>

      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="h-12 border-b flex items-center justify-end gap-3 px-4">
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
              <span className="text-xs text-muted-foreground tabular-nums">
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
