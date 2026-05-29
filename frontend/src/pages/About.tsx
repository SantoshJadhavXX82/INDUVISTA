/**
 * About — product identity, version, environment, and a copyable diagnostics
 * block for support. Pulls live facts from /health (version, env, DB latency,
 * migration head, uptime) so a support screenshot is self-describing.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Activity, Copy, Check, ExternalLink } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";

type Health = {
  status: string;
  app_name: string;
  app_env: string;
  app_timezone: string;
  role: string;
  db_latency_ms: number;
  migration_version: string | null;
  uptime_sec: number;
  started_at: string;
  version?: string;
};

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between py-1.5 text-sm" style={{ borderBottom: "0.5px solid var(--separator)" }}>
      <span style={{ color: "var(--text-secondary)" }}>{label}</span>
      <span className="font-medium text-right" style={{ color: "var(--text-primary)" }}>{value}</span>
    </div>
  );
}

function fmtUptime(sec: number): string {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function About() {
  const health = useQuery({
    queryKey: ["health-about"],
    queryFn: () => fetch("/health").then((r) => r.json() as Promise<Health>),
    refetchInterval: 30_000,
  });
  const [copied, setCopied] = useState(false);
  const h = health.data;

  const FRONTEND_VERSION = "0.3.0"; // kept in step with backend app.version

  function copyDiagnostics() {
    const lines = [
      `InduVista — Industrial Reporting Tool`,
      `Frontend: ${FRONTEND_VERSION}`,
      `Backend:  ${h?.version ?? "unknown"}`,
      `Env:      ${h?.app_env ?? "?"}`,
      `Node role:${h?.role ?? "?"}`,
      `DB migration head: ${h?.migration_version ?? "?"}`,
      `DB latency: ${h?.db_latency_ms ?? "?"} ms`,
      `Uptime:    ${h ? fmtUptime(h.uptime_sec) : "?"}`,
      `Timezone:  ${h?.app_timezone ?? "?"}`,
      `Captured:  ${new Date().toISOString()}`,
    ].join("\n");
    navigator.clipboard.writeText(lines).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="space-y-4 max-w-3xl mx-auto">
      <PageHeader title="About" subtitle="Product version, environment, and support information" />

      <SectionCard>
        <div className="flex items-center gap-3 mb-4">
          <div className="shrink-0 flex items-center justify-center"
               style={{ width: 44, height: 44, backgroundColor: "var(--ios-blue)", borderRadius: 10 }}>
            <Activity className="text-white" style={{ width: 26, height: 26 }} strokeWidth={2.5} />
          </div>
          <div>
            <div className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>InduVista</div>
            <div className="text-xs uppercase tracking-wider" style={{ color: "var(--text-secondary)" }}>Industrial Reporting Tool</div>
          </div>
        </div>

        <Row label="Frontend version" value={FRONTEND_VERSION} />
        <Row label="Backend version" value={h?.version ?? (health.isLoading ? "…" : "unknown")} />
        <Row label="Environment" value={
          <span className="px-2 py-0.5 rounded text-xs" style={{
            backgroundColor: h?.app_env === "production" ? "var(--ios-green-soft, rgba(52,199,89,0.12))" : "var(--ios-yellow-soft, rgba(255,204,0,0.12))",
            color: h?.app_env === "production" ? "var(--ios-green, #34c759)" : "var(--ios-orange, #ff9500)",
          }}>{h?.app_env ?? "?"}</span>
        } />
        <Row label="Node role" value={h?.role ?? "?"} />
        <Row label="DB migration head" value={<span className="font-mono text-xs">{h?.migration_version ?? "?"}</span>} />
        <Row label="DB latency" value={`${h?.db_latency_ms ?? "?"} ms`} />
        <Row label="Uptime" value={h ? fmtUptime(h.uptime_sec) : "?"} />
        <Row label="Plant timezone" value={h?.app_timezone ?? "?"} />

        <button
          onClick={copyDiagnostics}
          className="mt-4 inline-flex items-center gap-2 px-3 py-1.5 rounded text-sm"
          style={{ backgroundColor: "var(--ios-blue)", color: "white" }}
        >
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          {copied ? "Copied" : "Copy diagnostics for support"}
        </button>
      </SectionCard>

      <SectionCard>
        <div className="text-sm font-medium mb-2" style={{ color: "var(--text-primary)" }}>Open-source acknowledgements</div>
        <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          Built with FastAPI, SQLAlchemy, PostgreSQL + TimescaleDB, Valkey, React, Vite,
          TanStack Query, uPlot, Tailwind, and Lucide icons. Modbus and OPC UA acquisition
          via pymodbus and asyncua. Grateful to the maintainers of these projects.
        </p>
      </SectionCard>

      <SectionCard>
        <div className="text-sm font-medium mb-2" style={{ color: "var(--text-primary)" }}>License & support</div>
        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
          © {new Date().getFullYear()} SVJ. Licensed for the deployed plant installation.
          For support, capture the diagnostics above and contact your system administrator.
        </p>
        <a href="/help" className="mt-2 inline-flex items-center gap-1 text-xs hover:underline" style={{ color: "var(--ios-blue)" }}>
          Open Help <ExternalLink className="h-3 w-3" />
        </a>
      </SectionCard>
    </div>
  );
}
