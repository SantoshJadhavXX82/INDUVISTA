/**
 * Phase 6 — Diagnostics view.
 *
 * Wires the seven /api/diagnostics/* endpoints into a single page:
 *   - /summary             → three summary cards at top
 *   - /worker-status       → per-device runtime table
 *   - /buffer-health       → store-and-forward status card
 *   - /tag-overlaps        → collapsible issues table (shown only if non-empty)
 *   - /tag-block-fit       → collapsible issues table (shown only if non-empty)
 *   - /stale-tags          → collapsible stale-tags table (shown only if non-empty)
 *
 * Every query auto-refetches every 5 seconds so the page is "live" without
 * websockets or polling logic in user code.
 */
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, AlertTriangle, XCircle, Activity, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import {
  type DiagnosticsSummary,
  type WorkerDeviceStatus,
  type BufferHealth,
  type TagOverlap,
  type TagBlockFitIssue,
  type StaleTag,
} from "@/types/api";
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
import { cn } from "@/lib/utils";
import { HelpTip } from "@/components/ui/help-tip";
import { help } from "@/lib/help-text";

const REFRESH_MS = 5_000;

export default function Diagnostics() {
  const summary = useQuery({
    queryKey: ["diagnostics", "summary"],
    queryFn: () => api.get<DiagnosticsSummary>("/diagnostics/summary"),
    refetchInterval: REFRESH_MS,
  });

  const workers = useQuery({
    queryKey: ["diagnostics", "worker-status"],
    queryFn: () => api.get<WorkerDeviceStatus[]>("/diagnostics/worker-status"),
    refetchInterval: REFRESH_MS,
  });

  const buffer = useQuery({
    queryKey: ["diagnostics", "buffer-health"],
    queryFn: () => api.get<BufferHealth>("/diagnostics/buffer-health"),
    refetchInterval: REFRESH_MS,
  });

  const overlaps = useQuery({
    queryKey: ["diagnostics", "tag-overlaps"],
    queryFn: () => api.get<TagOverlap[]>("/diagnostics/tag-overlaps"),
    refetchInterval: REFRESH_MS * 6, // config issues don't change fast
  });

  const blockFit = useQuery({
    queryKey: ["diagnostics", "tag-block-fit"],
    queryFn: () => api.get<TagBlockFitIssue[]>("/diagnostics/tag-block-fit"),
    refetchInterval: REFRESH_MS * 6,
  });

  const staleTags = useQuery({
    queryKey: ["diagnostics", "stale-tags"],
    queryFn: () => api.get<StaleTag[]>("/diagnostics/stale-tags"),
    refetchInterval: REFRESH_MS,
  });

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Diagnostics</h1>
          <p className="text-sm text-muted-foreground mt-1">
            System health at a glance. Auto-refreshes every 5 seconds.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <RefreshCw className={cn("h-3 w-3", (summary.isFetching || workers.isFetching) && "animate-spin")} />
          <span>live</span>
        </div>
      </div>

      {/* Three summary cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <WorkersCard summary={summary.data} />
        <BufferCard buffer={buffer.data} />
        <ConfigIssuesCard summary={summary.data} />
      </div>

      {/* Worker status table */}
      <Card>
        <CardHeader>
          <CardTitle>Workers</CardTitle>
        </CardHeader>
        <CardContent>
          {workers.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : workers.data && workers.data.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Device</TableHead>
                  <TableHead>
                    Connection <HelpTip entry={help.diagnostics.worker_status} />
                  </TableHead>
                  <TableHead className="text-right">
                    Last cycle (s ago) <HelpTip entry={help.diagnostics.last_seen} />
                  </TableHead>
                  <TableHead className="text-right">Samples</TableHead>
                  <TableHead className="text-right">Good %</TableHead>
                  <TableHead className="text-right">Total (since restart)</TableHead>
                  <TableHead className="text-right">
                    Consec. failures <HelpTip entry={help.diagnostics.error_rate} />
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {workers.data.map((w) => (
                  <TableRow key={w.device_id}>
                    <TableCell className="font-medium">{w.device_name}</TableCell>
                    <TableCell>
                      <ConnectionBadge state={w.connection_state} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {w.seconds_since_last_cycle != null
                        ? w.seconds_since_last_cycle.toFixed(1)
                        : "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {w.last_cycle_samples_good ?? 0} / {w.last_cycle_samples_total ?? 0}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {(() => {
                        const total = w.last_cycle_samples_total ?? 0;
                        if (total === 0) return <span className="text-muted-foreground">—</span>;
                        const pct = ((w.last_cycle_samples_good ?? 0) / total) * 100;
                        return (
                          <span className={cn(
                            pct < 100 && "text-amber-700",
                            pct < 50 && "text-red-700 font-semibold",
                          )}>
                            {pct.toFixed(1)}%
                          </span>
                        );
                      })()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-xs"
                               title="Cumulative since worker startup; resets when the modbus_worker container restarts.">
                      <div className="font-medium">
                        {w.cumulative_samples_total.toLocaleString()}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        {w.cumulative_samples_good.toLocaleString()} good
                        {w.cumulative_samples_total > 0 && (
                          <> · {((w.cumulative_samples_good / w.cumulative_samples_total) * 100).toFixed(2)}%</>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <span className={cn(w.consecutive_failures > 0 && "text-red-700 font-semibold")}>
                        {w.consecutive_failures}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">
              No worker has reported yet. Has the modbus_worker container started?
            </p>
          )}
        </CardContent>
      </Card>

      {/* Buffer details */}
      {buffer.data && <BufferDetailsCard buffer={buffer.data} />}

      {/* Issue sections — only rendered when non-empty */}
      {staleTags.data && staleTags.data.length > 0 && (
        <StaleTagsCard tags={staleTags.data} />
      )}
      {overlaps.data && overlaps.data.length > 0 && (
        <OverlapsCard overlaps={overlaps.data} />
      )}
      {blockFit.data && blockFit.data.length > 0 && (
        <BlockFitCard issues={blockFit.data} />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Summary cards
// --------------------------------------------------------------------------

function WorkersCard({ summary }: { summary?: DiagnosticsSummary }) {
  const healthy = summary?.workers_healthy ?? 0;
  const unhealthy = summary?.workers_unhealthy ?? 0;
  const total = healthy + unhealthy;
  const allOk = total > 0 && unhealthy === 0;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Activity className="h-4 w-4" />
          Workers
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-baseline gap-2">
          <span className="text-3xl font-bold tabular-nums">{healthy}</span>
          <span className="text-sm text-muted-foreground">/ {total} healthy</span>
        </div>
        <div className="mt-2">
          {summary == null ? (
            <Badge variant="secondary">…</Badge>
          ) : allOk ? (
            <Badge variant="success">all polling cleanly</Badge>
          ) : unhealthy > 0 ? (
            <Badge variant="destructive">{unhealthy} unhealthy</Badge>
          ) : (
            <Badge variant="warning">no workers reporting</Badge>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function BufferCard({ buffer }: { buffer?: BufferHealth }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Store-and-forward buffer</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-baseline gap-2">
          <span className="text-3xl font-bold tabular-nums">
            {buffer?.backlog ?? "—"}
          </span>
          <span className="text-sm text-muted-foreground">samples queued</span>
        </div>
        <div className="mt-2">
          {buffer == null ? (
            <Badge variant="secondary">…</Badge>
          ) : (
            <Badge
              variant={
                buffer.status === "healthy"
                  ? "success"
                  : buffer.status === "buffering"
                    ? "warning"
                    : "destructive"
              }
            >
              {buffer.status}
            </Badge>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ConfigIssuesCard({ summary }: { summary?: DiagnosticsSummary }) {
  const totalIssues =
    (summary?.overlap_count ?? 0) +
    (summary?.block_fit_issue_count ?? 0) +
    (summary?.stale_tag_count ?? 0);
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Config & data issues</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-baseline gap-2">
          <span className="text-3xl font-bold tabular-nums">
            {summary != null ? totalIssues : "—"}
          </span>
          <span className="text-sm text-muted-foreground">issues</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {summary == null ? (
            <Badge variant="secondary">…</Badge>
          ) : totalIssues === 0 ? (
            <Badge variant="success">clean</Badge>
          ) : (
            <>
              {summary.overlap_count > 0 && (
                <Badge variant="destructive">{summary.overlap_count} overlap</Badge>
              )}
              {summary.block_fit_issue_count > 0 && (
                <Badge variant="destructive">
                  {summary.block_fit_issue_count} block-fit
                </Badge>
              )}
              {summary.stale_tag_count > 0 && (
                <Badge variant="warning">{summary.stale_tag_count} stale</Badge>
              )}
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Connection badge
// --------------------------------------------------------------------------

function ConnectionBadge({ state }: { state: WorkerDeviceStatus["connection_state"] }) {
  if (state === "connected") {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" />
        connected
      </Badge>
    );
  }
  if (state === "reconnecting") {
    return (
      <Badge variant="warning" className="gap-1">
        <RefreshCw className="h-3 w-3 animate-spin" />
        reconnecting
      </Badge>
    );
  }
  return (
    <Badge variant="destructive" className="gap-1">
      <XCircle className="h-3 w-3" />
      disconnected
    </Badge>
  );
}

// --------------------------------------------------------------------------
// Detail cards (issues, only shown when non-empty)
// --------------------------------------------------------------------------

function BufferDetailsCard({ buffer }: { buffer: BufferHealth }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Buffer details</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
          <div>
            <dt className="text-muted-foreground">Status</dt>
            <dd className="font-medium mt-0.5">{buffer.status}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Backlog</dt>
            <dd className="font-medium tabular-nums mt-0.5">{buffer.backlog}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Oldest sample age</dt>
            <dd className="font-medium tabular-nums mt-0.5">
              {buffer.oldest_sample_age_seconds != null
                ? `${buffer.oldest_sample_age_seconds.toFixed(0)} s`
                : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Last replay at</dt>
            <dd className="font-medium tabular-nums mt-0.5">
              {buffer.last_replay_at ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Last replay count</dt>
            <dd className="font-medium tabular-nums mt-0.5">
              {buffer.last_replay_count ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Updated</dt>
            <dd className="font-medium tabular-nums mt-0.5">
              {new Date(buffer.updated_at).toLocaleTimeString()}
            </dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  );
}

function StaleTagsCard({ tags }: { tags: StaleTag[] }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-600" />
          Stale tags ({tags.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Tag</TableHead>
              <TableHead>Device</TableHead>
              <TableHead className="text-right">
                Age (s) <HelpTip entry={help.diagnostics.last_seen} />
              </TableHead>
              <TableHead>
                ST <HelpTip entry={help.diagnostics.st_status} />
              </TableHead>
              <TableHead>Reason</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tags.slice(0, 50).map((t) => (
              <TableRow key={t.tag_id}>
                <TableCell className="font-medium">{t.tag_name}</TableCell>
                <TableCell>{t.device_name}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {t.age_seconds.toFixed(0)}
                </TableCell>
                <TableCell className="tabular-nums">{t.st}</TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {t.st_reason ?? "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {tags.length > 50 && (
          <p className="text-xs text-muted-foreground mt-2">
            Showing 50 of {tags.length} stale tags.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function OverlapsCard({ overlaps }: { overlaps: TagOverlap[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <XCircle className="h-4 w-4 text-red-600" />
          Tag address overlaps ({overlaps.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Device</TableHead>
              <TableHead>FC</TableHead>
              <TableHead>Tag A</TableHead>
              <TableHead>Range</TableHead>
              <TableHead>Tag B</TableHead>
              <TableHead>Range</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {overlaps.map((o, i) => (
              <TableRow key={i}>
                <TableCell>{o.device_name}</TableCell>
                <TableCell className="tabular-nums">{o.function_code}</TableCell>
                <TableCell className="font-medium">{o.tag1_name}</TableCell>
                <TableCell className="tabular-nums text-xs text-muted-foreground">
                  {o.tag1_address}–{o.tag1_address + o.tag1_register_count - 1}
                </TableCell>
                <TableCell className="font-medium">{o.tag2_name}</TableCell>
                <TableCell className="tabular-nums text-xs text-muted-foreground">
                  {o.tag2_address}–{o.tag2_address + o.tag2_register_count - 1}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function BlockFitCard({ issues }: { issues: TagBlockFitIssue[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <XCircle className="h-4 w-4 text-red-600" />
          Block-fit issues ({issues.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Tag</TableHead>
              <TableHead>Block</TableHead>
              <TableHead>Issue</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {issues.map((i) => (
              <TableRow key={i.tag_id}>
                <TableCell className="font-medium">{i.tag_name}</TableCell>
                <TableCell>{i.block_name}</TableCell>
                <TableCell className="text-xs">{i.issue}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
