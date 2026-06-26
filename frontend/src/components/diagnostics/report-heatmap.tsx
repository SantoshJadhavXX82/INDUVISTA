/**
 * Report Health heatmap — Reports x Time and Reports x Stage, click for detail.
 *
 * A (time):  GET /api/report-config/jobs            -> outcome per (report, time bucket)
 * B (stage): GET /api/report-config/stage-summary   -> health/ms per (report, stage)
 * detail:    GET /api/report-config/jobs/{id}/stages (lazy, on cell click in A)
 */
import { useMemo, useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { LayoutGrid, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface ReportJob {
  id: number; report_id: number; report_name: string | null; status: string;
  snapshot_at: string | null; created_at: string; trigger_kind: string;
  formats: string | null; duration_ms: number | null; error: string | null;
}
interface JobStage {
  id: number; job_id: number; seq: number; stage: string; status: string;
  n: number | null; bytes: number | null; ms: number | null; detail: string | null;
}
interface StageSummary {
  report_id: number; report_name: string | null; stage: string;
  runs: number; ok: number; err: number; partial: number;
  avg_ms: number | null; max_ms: number | null; total_bytes: number | null;
}

const STAGES = ["resolve", "data", "render", "deliver"];
const REFRESH_MS = 30_000;

function cellBg(status: string | null | undefined): string {
  switch (status) {
    case "succeeded": case "ok": return "bg-green-500";
    case "partial":            return "bg-amber-400";
    case "failed": case "error": return "bg-red-500";
    case "missed":             return "bg-orange-400";
    case "running": case "queued": return "bg-blue-400";
    default:                   return "bg-muted";
  }
}
function statusLabel(status: string): string {
  const m: Record<string, string> = {
    succeeded: "Succeeded", ok: "OK", partial: "Partial", failed: "Failed",
    error: "Error", missed: "Missed", running: "Running", queued: "Queued",
    skipped: "Skipped",
  };
  return m[status] ?? status;
}
function fmtBytes(n: number | null): string {
  if (n == null) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}
function fmtWhen(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

interface Bucket { start: number; end: number; label: string; }
function buildBuckets(win: "24h" | "14d"): Bucket[] {
  const out: Bucket[] = [];
  const now = new Date();
  if (win === "24h") {
    const top = new Date(now); top.setMinutes(0, 0, 0);
    for (let i = 23; i >= 0; i--) {
      const s = top.getTime() - i * 3_600_000;
      out.push({ start: s, end: s + 3_600_000, label: new Date(s).toLocaleTimeString([], { hour: "2-digit" }) });
    }
  } else {
    const top = new Date(now); top.setHours(0, 0, 0, 0);
    for (let i = 13; i >= 0; i--) {
      const s = top.getTime() - i * 86_400_000;
      out.push({ start: s, end: s + 86_400_000, label: new Date(s).toLocaleDateString([], { month: "short", day: "numeric" }) });
    }
  }
  return out;
}

function RunDetail({ jobId, job }: { jobId: number; job: ReportJob | undefined }) {
  const q = useQuery({
    queryKey: ["report-heatmap", "stages", jobId],
    queryFn: () => api.get<JobStage[]>(`/report-config/jobs/${jobId}/stages`),
  });
  return (
    <div className="space-y-2">
      {job && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="outline" className={cn("border", cellBg(job.status), "text-white border-transparent")}>{statusLabel(job.status)}</Badge>
          <span className="font-medium">{job.report_name ?? `Report ${job.report_id}`}</span>
          <span className="text-muted-foreground">{fmtWhen(job.snapshot_at ?? job.created_at)}</span>
          <span className="text-muted-foreground">{job.trigger_kind}</span>
          {job.formats && <span className="text-muted-foreground">{job.formats}</span>}
          {job.error && <span className="text-red-600 dark:text-red-400">{job.error}</span>}
        </div>
      )}
      {q.isLoading ? (
        <span className="text-xs text-muted-foreground">Loading stages…</span>
      ) : (q.data ?? []).length === 0 ? (
        <span className="text-xs text-muted-foreground">No per-stage telemetry for this run.</span>
      ) : (
        <div className="flex flex-wrap gap-2">
          {(q.data ?? []).map((s) => (
            <div key={s.id} className="rounded-md border px-2 py-1 text-xs">
              <span className="font-medium capitalize">{s.stage}</span>
              <span className={cn("ml-1 inline-block h-2 w-2 rounded-full align-middle", cellBg(s.status))} />
              <span className="ml-2 text-muted-foreground tabular-nums">
                {s.n != null ? `n=${s.n}` : ""}{s.bytes != null ? ` · ${fmtBytes(s.bytes)}` : ""}{s.ms != null ? ` · ${s.ms} ms` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ReportHeatmapCard() {
  const [view, setView] = useState<"time" | "stage">("time");
  const [win, setWin] = useState<"24h" | "14d">("24h");
  const [metric, setMetric] = useState<"health" | "time">("health");
  const [sel, setSel] = useState<{ kind: "run"; jobId: number } | { kind: "stage"; key: string } | null>(null);

  const jobs = useQuery({
    queryKey: ["report-heatmap", "jobs"],
    queryFn: () => api.get<ReportJob[]>("/report-config/jobs?limit=500"),
    refetchInterval: REFRESH_MS,
    enabled: view === "time",
  });
  const summary = useQuery({
    queryKey: ["report-heatmap", "summary"],
    queryFn: () => api.get<StageSummary[]>("/report-config/stage-summary"),
    refetchInterval: REFRESH_MS,
    enabled: view === "stage",
  });

  // ---- view A: reports x time ----
  const buckets = useMemo(() => buildBuckets(win), [win]);
  const timeReports = useMemo(() => {
    const m = new Map<number, string>();
    for (const j of jobs.data ?? []) if (!m.has(j.report_id)) m.set(j.report_id, j.report_name ?? `Report ${j.report_id}`);
    return Array.from(m.entries()).sort((a, b) => a[1].localeCompare(b[1]));
  }, [jobs.data]);
  // grid[reportId][bucketIdx] = latest job in that bucket
  const grid = useMemo(() => {
    const g = new Map<number, (ReportJob | undefined)[]>();
    for (const [rid] of timeReports) g.set(rid, new Array(buckets.length).fill(undefined));
    for (const j of jobs.data ?? []) {
      const t = new Date(j.snapshot_at ?? j.created_at).getTime();
      if (isNaN(t)) continue;
      const bi = buckets.findIndex((b) => t >= b.start && t < b.end);
      if (bi < 0) continue;
      const row = g.get(j.report_id); if (!row) continue;
      const cur = row[bi];
      if (!cur || j.id > cur.id) row[bi] = j;
    }
    return g;
  }, [jobs.data, buckets, timeReports]);
  const jobById = useMemo(() => {
    const m = new Map<number, ReportJob>();
    for (const j of jobs.data ?? []) m.set(j.id, j);
    return m;
  }, [jobs.data]);

  // ---- view B: reports x stage ----
  const stageReports = useMemo(() => {
    const m = new Map<number, string>();
    for (const s of summary.data ?? []) if (!m.has(s.report_id)) m.set(s.report_id, s.report_name ?? `Report ${s.report_id}`);
    return Array.from(m.entries()).sort((a, b) => a[1].localeCompare(b[1]));
  }, [summary.data]);
  const summaryByKey = useMemo(() => {
    const m = new Map<string, StageSummary>();
    for (const s of summary.data ?? []) m.set(`${s.report_id}:${s.stage}`, s);
    return m;
  }, [summary.data]);
  const maxMs = useMemo(() => Math.max(1, ...((summary.data ?? []).map((s) => s.avg_ms ?? 0))), [summary.data]);

  function stageHealth(s: StageSummary | undefined): string {
    if (!s) return "bg-muted";
    if (s.err > 0) return "bg-red-500";
    if (s.partial > 0) return "bg-amber-400";
    if (s.ok > 0) return "bg-green-500";
    return "bg-muted";
  }
  function heatStyle(ms: number | null | undefined): CSSProperties {
    if (ms == null) return {};
    const frac = Math.min(1, ms / maxMs);
    return { backgroundColor: `hsl(${Math.round(120 - 120 * frac)}, 65%, 45%)` };
  }

  const fetching = view === "time" ? jobs.isFetching : summary.isFetching;
  const loading = view === "time" ? jobs.isLoading : summary.isLoading;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <LayoutGrid className="h-4 w-4" /> Report heatmap
          </CardTitle>
          <div className="flex items-center gap-2">
            {fetching && <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />}
            <div className="flex rounded-md border text-xs overflow-hidden">
              <button onClick={() => { setView("time"); setSel(null); }}
                className={cn("px-2 h-7", view === "time" ? "bg-muted font-medium" : "text-muted-foreground")}>Time</button>
              <button onClick={() => { setView("stage"); setSel(null); }}
                className={cn("px-2 h-7 border-l", view === "stage" ? "bg-muted font-medium" : "text-muted-foreground")}>Stage</button>
            </div>
            {view === "time" ? (
              <div className="flex rounded-md border text-xs overflow-hidden">
                <button onClick={() => setWin("24h")} className={cn("px-2 h-7", win === "24h" ? "bg-muted font-medium" : "text-muted-foreground")}>24h</button>
                <button onClick={() => setWin("14d")} className={cn("px-2 h-7 border-l", win === "14d" ? "bg-muted font-medium" : "text-muted-foreground")}>14d</button>
              </div>
            ) : (
              <div className="flex rounded-md border text-xs overflow-hidden">
                <button onClick={() => setMetric("health")} className={cn("px-2 h-7", metric === "health" ? "bg-muted font-medium" : "text-muted-foreground")}>Health</button>
                <button onClick={() => setMetric("time")} className={cn("px-2 h-7 border-l", metric === "time" ? "bg-muted font-medium" : "text-muted-foreground")}>Time</button>
              </div>
            )}
          </div>
        </div>
        {/* legend */}
        <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
          {view === "time" || metric === "health" ? (
            <>
              <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm bg-green-500" /> ok</span>
              <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm bg-amber-400" /> partial</span>
              <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm bg-red-500" /> failed</span>
              <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm bg-orange-400" /> missed</span>
              <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm bg-muted border" /> none</span>
            </>
          ) : (
            <span className="flex items-center gap-1">avg ms: fast
              <span className="h-2.5 w-8 rounded-sm" style={{ background: "linear-gradient(90deg, hsl(120,65%,45%), hsl(60,65%,45%), hsl(0,65%,45%))" }} /> slow</span>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : view === "time" ? (
          timeReports.length === 0 ? (
            <p className="text-sm text-muted-foreground">No report runs recorded yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="border-separate" style={{ borderSpacing: "2px" }}>
                <thead>
                  <tr>
                    <th className="sticky left-0 bg-background"></th>
                    {buckets.map((b, i) => (
                      <th key={i} className="text-[9px] text-muted-foreground font-normal align-bottom h-12">
                        <div className="origin-bottom-left -rotate-45 translate-y-1 whitespace-nowrap">{b.label}</div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {timeReports.map(([rid, name]) => (
                    <tr key={rid}>
                      <td className="sticky left-0 bg-background pr-2 text-xs font-medium whitespace-nowrap max-w-[14rem] truncate">{name}</td>
                      {(grid.get(rid) ?? []).map((j, i) => (
                        <td key={i}>
                          <button
                            title={j ? `${statusLabel(j.status)} · ${fmtWhen(j.snapshot_at ?? j.created_at)}` : "no run"}
                            onClick={() => j && setSel({ kind: "run", jobId: j.id })}
                            disabled={!j}
                            className={cn("h-5 w-5 rounded-sm", cellBg(j?.status),
                              j ? "cursor-pointer hover:ring-2 hover:ring-ring" : "opacity-40 cursor-default",
                              sel && sel.kind === "run" && j && sel.jobId === j.id ? "ring-2 ring-ring" : "")}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : stageReports.length === 0 ? (
          <p className="text-sm text-muted-foreground">No per-stage telemetry yet (no scheduled runs since instrumentation).</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="border-separate" style={{ borderSpacing: "2px" }}>
              <thead>
                <tr>
                  <th className="sticky left-0 bg-background"></th>
                  {STAGES.map((st) => (
                    <th key={st} className="text-[10px] text-muted-foreground font-normal capitalize px-1">{st}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {stageReports.map(([rid, name]) => (
                  <tr key={rid}>
                    <td className="sticky left-0 bg-background pr-2 text-xs font-medium whitespace-nowrap max-w-[14rem] truncate">{name}</td>
                    {STAGES.map((st) => {
                      const key = `${rid}:${st}`;
                      const s = summaryByKey.get(key);
                      const isSel = sel && sel.kind === "stage" && sel.key === key;
                      const style = metric === "time" ? heatStyle(s?.avg_ms) : {};
                      return (
                        <td key={st}>
                          <button
                            title={s ? `${st}: ${s.ok} ok / ${s.err} err / ${s.partial} partial · avg ${s.avg_ms ?? "-"} ms` : "no data"}
                            onClick={() => s && setSel({ kind: "stage", key })}
                            disabled={!s}
                            style={style}
                            className={cn("h-7 w-16 rounded-sm text-[10px] text-white tabular-nums",
                              metric === "time" ? "" : stageHealth(s),
                              s ? "cursor-pointer hover:ring-2 hover:ring-ring" : "bg-muted opacity-40 cursor-default",
                              isSel ? "ring-2 ring-ring" : "")}
                          >
                            {s ? (metric === "time" ? `${s.avg_ms ?? "-"}ms` : `${s.ok}/${s.runs}`) : ""}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* detail drawer */}
        {sel && (
          <div className="rounded-lg border p-3 bg-muted/20">
            {sel.kind === "run" ? (
              <RunDetail jobId={sel.jobId} job={jobById.get(sel.jobId)} />
            ) : (() => {
              const s = summaryByKey.get(sel.key);
              if (!s) return <span className="text-xs text-muted-foreground">No data.</span>;
              return (
                <div className="flex flex-wrap items-center gap-3 text-xs">
                  <span className="font-medium capitalize">{s.stage}</span>
                  <span className="text-muted-foreground">{s.report_name ?? `Report ${s.report_id}`}</span>
                  <span>{s.runs} run(s)</span>
                  <span className="text-green-600 dark:text-green-400">{s.ok} ok</span>
                  {s.partial ? <span className="text-amber-600 dark:text-amber-400">{s.partial} partial</span> : null}
                  {s.err ? <span className="text-red-600 dark:text-red-400">{s.err} err</span> : null}
                  <span className="text-muted-foreground tabular-nums">avg {s.avg_ms ?? "-"} ms · max {s.max_ms ?? "-"} ms · {fmtBytes(s.total_bytes)}</span>
                </div>
              );
            })()}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
