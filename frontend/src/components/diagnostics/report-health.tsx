/**
 * Report Health — run-history panel for the Diagnostics page.
 *
 * Reads the report run log (report_jobs) via:
 *   GET /api/report-config/jobs                   (all reports, recent)
 *   GET /api/report-config/definitions/{id}/jobs  (one report, drill-down)
 *
 * Surfaces each run with a status badge — including the "Missed" status
 * (a scheduled occurrence that came due while the scheduler was down/behind
 * and was too stale to run; recorded, not generated). Read-only for now;
 * a future "Re-run" action will live here.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";

interface ReportJob {
  id: number;
  report_id: number;
  report_name: string | null;
  trigger_kind: string;
  revision_id: number | null;
  formats: string | null;
  status: string;
  snapshot_at: string | null;
  period_start: string | null;
  period_end: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
}

const REFRESH_MS = 15_000;

function statusStyle(status: string): { label: string; className: string } {
  switch (status) {
    case "succeeded": return { label: "Succeeded", className: "border-green-300 text-green-700 dark:text-green-400" };
    case "partial":   return { label: "Partial",   className: "border-amber-300 text-amber-700 dark:text-amber-400" };
    case "failed":    return { label: "Failed",    className: "border-red-300 text-red-700 dark:text-red-400" };
    case "missed":    return { label: "Missed",    className: "border-orange-300 text-orange-700 dark:text-orange-400" };
    case "running":   return { label: "Running",   className: "border-blue-300 text-blue-700 dark:text-blue-400" };
    case "queued":    return { label: "Queued",    className: "border-muted-foreground/30 text-muted-foreground" };
    default:          return { label: status || "-", className: "border-muted-foreground/30 text-muted-foreground" };
  }
}

function fmtWhen(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function ReportHealthCard() {
  const [reportId, setReportId] = useState<number | "all">("all");
  const [issuesOnly, setIssuesOnly] = useState(false);

  // All recent jobs — always loaded; also the source for the report dropdown.
  const allJobs = useQuery({
    queryKey: ["report-health", "all"],
    queryFn: () => api.get<ReportJob[]>("/report-config/jobs?limit=200"),
    refetchInterval: REFRESH_MS,
  });

  // Per-report drill-down — only when a specific report is selected.
  const oneReport = useQuery({
    queryKey: ["report-health", "one", reportId],
    queryFn: () => api.get<ReportJob[]>(`/report-config/definitions/${reportId}/jobs?limit=200`),
    enabled: reportId !== "all",
    refetchInterval: REFRESH_MS,
  });

  const active = reportId === "all" ? allJobs : oneReport;
  const rawRows = (active.data ?? []) as ReportJob[];

  // Distinct reports seen in the recent log -> drill-down options.
  const reportOptions = useMemo(() => {
    const seen = new Map<number, string>();
    for (const j of allJobs.data ?? []) {
      if (!seen.has(j.report_id)) seen.set(j.report_id, j.report_name ?? `Report ${j.report_id}`);
    }
    return Array.from(seen.entries()).sort((a, b) => a[1].localeCompare(b[1]));
  }, [allJobs.data]);

  const rows = useMemo(() => {
    if (!issuesOnly) return rawRows;
    return rawRows.filter((r) => r.status === "failed" || r.status === "missed" || r.status === "partial");
  }, [rawRows, issuesOnly]);

  // Counts across the unfiltered current view.
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rawRows) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [rawRows]);

  const showReportCol = reportId === "all";

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <FileText className="h-4 w-4" /> Report health
          </CardTitle>
          <div className="flex items-center gap-2">
            {active.isFetching && <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />}
            <select
              value={String(reportId)}
              onChange={(e) => setReportId(e.target.value === "all" ? "all" : Number(e.target.value))}
              className="h-8 rounded-md border bg-background px-2 text-xs"
            >
              <option value="all">All reports</option>
              {reportOptions.map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </select>
            <button
              onClick={() => setIssuesOnly((v) => !v)}
              className={cn(
                "h-8 rounded-md border px-2 text-xs font-medium transition-colors",
                issuesOnly ? "border-red-300 text-red-700 dark:text-red-400" : "text-muted-foreground",
              )}
            >
              {issuesOnly ? "Failed & missed" : "All statuses"}
            </button>
          </div>
        </div>
        <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted-foreground">
          <span>{rawRows.length} run(s)</span>
          {counts.succeeded ? <span>{counts.succeeded} ok</span> : null}
          {counts.partial ? <span className="text-amber-600 dark:text-amber-400">{counts.partial} partial</span> : null}
          {counts.failed ? <span className="text-red-600 dark:text-red-400">{counts.failed} failed</span> : null}
          {counts.missed ? <span className="text-orange-600 dark:text-orange-400">{counts.missed} missed</span> : null}
        </div>
      </CardHeader>
      <CardContent>
        {active.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading...</p>
        ) : active.isError ? (
          <p className="text-sm text-red-600 dark:text-red-400">Could not load run history.</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {issuesOnly ? "No failed or missed runs in the recent history." : "No report runs recorded yet."}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Status</TableHead>
                {showReportCol && <TableHead>Report</TableHead>}
                <TableHead>When</TableHead>
                <TableHead>Trigger</TableHead>
                <TableHead>Formats</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead className="text-xs text-muted-foreground">Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => {
                const s = statusStyle(r.status);
                return (
                  <TableRow key={r.id}>
                    <TableCell>
                      <Badge variant="outline" className={cn(s.className)}>{s.label}</Badge>
                    </TableCell>
                    {showReportCol && (
                      <TableCell className="font-medium">{r.report_name ?? `Report ${r.report_id}`}</TableCell>
                    )}
                    <TableCell className="text-xs text-muted-foreground tabular-nums">
                      {fmtWhen(r.snapshot_at ?? r.created_at)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{r.trigger_kind}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{r.formats ?? "-"}</TableCell>
                    <TableCell className="text-right text-xs text-muted-foreground tabular-nums">
                      {fmtDuration(r.duration_ms)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-[28rem] truncate" title={r.error ?? ""}>
                      {r.error ?? "-"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
