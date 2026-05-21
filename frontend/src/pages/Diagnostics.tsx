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
import { CheckCircle2, AlertTriangle, XCircle, Activity, RefreshCw, Cpu, HardDrive, Gauge as GaugeIcon, AlertOctagon } from "lucide-react";
import { api } from "@/lib/api";
import { formatFloat } from "@/lib/format";
import {
  type DiagnosticsSummary,
  type WorkerDeviceStatus,
  type BufferHealth,
  type TagOverlap,
  type TagBlockFitIssue,
  type StaleTag,
  type SystemStats,
  type OutOfRangeTag,
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
import { PageHeader } from "@/components/ui/page-header";
import { MetricStrip, type MetricItem } from "@/components/ui/metric-strip";
import { SectionCard } from "@/components/ui/section-card";
import { StatusPill } from "@/components/ui/status-pill";
import { QualityHeatmapCard } from "@/components/diagnostics/quality-heatmap";

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

  // Phase 12.6 — system resources (refreshed at the same cadence as the
  // other live cards). The endpoint is cheap to call: it reads /proc once
  // and returns the cached counters psutil already maintains.
  const sysStats = useQuery({
    queryKey: ["diagnostics", "system-stats"],
    queryFn: () => api.get<SystemStats>("/diagnostics/system-stats"),
    refetchInterval: REFRESH_MS,
  });

  // Phase 12.6 — operator-limit warnings. Slower cadence (15s) because
  // these are config-driven, not poll-driven; they don't change between
  // worker cycles except when an operator adjusts a min/max.
  const outOfRange = useQuery({
    queryKey: ["diagnostics", "out-of-range-tags"],
    queryFn: () => api.get<OutOfRangeTag[]>("/diagnostics/out-of-range-tags"),
    refetchInterval: REFRESH_MS * 3,
  });

  // Build the system overview MetricStrip from the summary data.
  // Phase 18 — replaces the three large WorkersCard / BufferCard /
  // ConfigIssuesCard with a uniform 4-card iOS strip. The detailed
  // breakdowns (per-worker table, buffer details, issue tables below)
  // still show full information; the strip is just the at-a-glance row.
  const summaryMetrics: MetricItem[] = (() => {
    const s = summary.data;
    const b = buffer.data;
    const workersTotal = (s?.workers_healthy ?? 0) + (s?.workers_unhealthy ?? 0);
    const totalIssues = (s?.overlap_count ?? 0)
                      + (s?.block_fit_issue_count ?? 0)
                      + (s?.stale_tag_count ?? 0);
    return [
      {
        label: "Workers",
        value: s ? `${s.workers_healthy}/${workersTotal}` : "—",
        tone: s == null ? "neutral"
          : s.workers_unhealthy === 0 ? "good"
          : "warn",
        hint: s == null
          ? undefined
          : s.workers_unhealthy === 0 ? "All healthy" : `${s.workers_unhealthy} unhealthy`,
      },
      {
        label: "SF buffer",
        value: b ? b.backlog.toLocaleString() : "—",
        tone: !b ? "neutral"
          : b.status === "healthy" ? "good"
          : b.status === "buffering" ? "warn"
          : "error",
        hint: b?.status,
      },
      {
        label: "Config issues",
        value: s ? totalIssues.toLocaleString() : "—",
        tone: s == null ? "neutral"
          : totalIssues === 0 ? "good"
          : totalIssues < 10 ? "warn"
          : "error",
        hint: s == null
          ? undefined
          : totalIssues === 0 ? "Clean"
          : breakdownLabel(s),
      },
      {
        label: "Enabled tags",
        value: s ? s.enabled_tag_count.toLocaleString() : "—",
        tone: "info",
        hint: s ? `${s.enabled_device_count} devices` : undefined,
      },
    ];
  })();

  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <PageHeader
        title="Health"
        subtitle="System overview · auto-refreshes every 5s"
        actions={
          <span
            className="text-xs flex items-center gap-1.5"
            style={{ color: "var(--ios-gray-1)" }}
          >
            <RefreshCw className={cn("h-3 w-3", (summary.isFetching || workers.isFetching) && "animate-spin")} />
            live
          </span>
        }
      />

      <MetricStrip items={summaryMetrics} />

      {/* Phase 19 — Quality heatmap. One-glance view of "is data healthy
          across all tags over the recent window?". Spot recurring issues:
          a horizontal red band = one chronically-broken tag; a vertical
          red column = a whole-system outage at that time. */}
      <QualityHeatmapCard />

      {/* Phase 12.6 — operator-limit warnings. Rendered only when non-empty
          so the page stays calm during normal operation. When something IS
          out of range, this lands above the workers table so the operator
          sees it before scrolling. */}
      {outOfRange.data && outOfRange.data.length > 0 && (
        <OutOfRangeCard rows={outOfRange.data} />
      )}

      {/* Worker status table */}
      <Card>
        <CardHeader>
          <CardTitle>Workers</CardTitle>
        </CardHeader>        <CardContent>
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

      {/* Phase 12.6 — system resources (CPU, memory, disk, top processes).
          Always rendered — operators want a constant view of the runtime,
          unlike config issues which only appear when there's a problem. */}
      {sysStats.data && <SystemResourcesCard stats={sysStats.data} />}

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

// --------------------------------------------------------------------------
// Phase 12.6 — System resources card
//
// Lays out CPU + memory as two bars in the top row, disks as one bar per
// mount in the second row, top processes as a compact table beneath. The
// goal is at-a-glance scannability: a fully green page = nothing to worry
// about, amber/red bands draw the eye to the first thing to look at.
// --------------------------------------------------------------------------

function SystemResourcesCard({ stats }: { stats: SystemStats }) {
  const isHost = stats.scope === "host";
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 flex-wrap">
          <Cpu className="h-4 w-4" />
          System resources
          {/* Scope badge — operators need to know whether they're looking
              at the host OS or the namespaced container view. */}
          <Badge
            variant="outline"
            className={cn(
              "text-[10px] font-medium",
              isHost
                ? "border-emerald-300 text-emerald-800 bg-emerald-50"
                : "border-amber-300 text-amber-800 bg-amber-50",
            )}
            title={
              isHost
                ? `Real host metrics from ${stats.hostname ?? "host"} (${stats.platform ?? "?"})`
                : "Container fallback — start host_agent for real host stats. See host_agent/README.md."
            }
          >
            {isHost ? "Host" : "Container fallback"}
          </Badge>
          {isHost && stats.hostname && (
            <span className="text-xs font-normal text-muted-foreground">
              {stats.hostname}
              {stats.platform && <> · {stats.platform}</>}
            </span>
          )}
          <span className="ml-auto text-xs font-normal text-muted-foreground tabular-nums">
            {isHost ? "host uptime" : "backend uptime"} {formatUptime(stats.uptime_sec)}
          </span>
        </CardTitle>
        {!isHost && (
          <p className="text-xs text-muted-foreground mt-1">
            Showing the backend container's view. To see real host CPU / RAM /
            drives (Task Manager / <code>top</code> parity), run the host agent —
            see <code>host_agent/README.md</code>.
            {stats.host_agent_last_seen_sec != null && (
              <> Last host-agent push: {stats.host_agent_last_seen_sec}s ago.</>
            )}
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-5">
        {/* CPU + Memory */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <UsageBar
            icon={<GaugeIcon className="h-4 w-4" />}
            label="CPU"
            percent={stats.cpu.percent}
            detail={
              `${stats.cpu.count_logical} logical core${stats.cpu.count_logical === 1 ? "" : "s"}` +
              (stats.cpu.count_physical
                ? ` · ${stats.cpu.count_physical} physical`
                : "") +
              (stats.cpu.load_average
                ? ` · load ${stats.cpu.load_average.map((n) => n.toFixed(2)).join(" / ")}`
                : "")
            }
          />
          <UsageBar
            icon={<Activity className="h-4 w-4" />}
            label="Memory"
            percent={stats.memory.percent}
            detail={
              // Task-Manager-style readout: used / available / total + cached.
              // Operators compare these to what their OS shows; matching the
              // vocabulary avoids "is this the right number?" confusion.
              `${formatBytes(stats.memory.used_bytes)} in use` +
              ` · ${formatBytes(stats.memory.available_bytes)} available` +
              ` · ${formatBytes(stats.memory.total_bytes)} total` +
              (stats.memory.cached_bytes > 0
                ? ` (${formatBytes(stats.memory.cached_bytes)} cached)`
                : "")
            }
          />
        </div>

        {/* GPUs — only when the host has them */}
        {stats.gpus.length > 0 && (
          <div className="space-y-3">
            <h4 className="text-xs uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <Cpu className="h-3 w-3" /> GPUs
            </h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4">
              {stats.gpus.map((g) => (
                <div key={g.index} className="space-y-2 rounded-md border p-3">
                  <div className="flex items-baseline justify-between">
                    <span className="font-medium text-sm">{g.name}</span>
                    <span className="text-xs text-muted-foreground">
                      GPU {g.index}
                      {g.temperature_c != null && <> · {g.temperature_c}°C</>}
                    </span>
                  </div>
                  <UsageBar
                    label="Utilization"
                    percent={g.utilization_percent}
                    detail={`${g.utilization_percent.toFixed(1)}% compute`}
                  />
                  <UsageBar
                    label="GPU Memory"
                    percent={g.memory_percent}
                    detail={`${formatBytes(g.memory_used_bytes)} of ${formatBytes(g.memory_total_bytes)} used`}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Disks — one bar per drive, with device + fstype detail */}
        {stats.disks.length > 0 && (
          <div className="space-y-3">
            <h4 className="text-xs uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <HardDrive className="h-3 w-3" /> Drive space
              <span className="font-normal text-muted-foreground/70 normal-case tracking-normal">
                ({stats.disks.length} {stats.disks.length === 1 ? "drive" : "drives"})
              </span>
            </h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3">
              {stats.disks.map((d) => (
                <UsageBar
                  key={`${d.mountpoint}:${d.device ?? ""}`}
                  label={
                    // On Windows mountpoint is "C:\", on Linux "/" etc.
                    // Strip trailing slash on Linux for compactness but
                    // keep the drive letter intact on Windows.
                    d.mountpoint.length > 1 && d.mountpoint.endsWith("/")
                      ? d.mountpoint.slice(0, -1)
                      : d.mountpoint
                  }
                  percent={d.percent}
                  detail={
                    `${formatBytes(d.used_bytes)} used · ${formatBytes(d.free_bytes)} free · ${formatBytes(d.total_bytes)} total` +
                    (d.fstype ? ` · ${d.fstype}` : "") +
                    (d.device ? ` · ${d.device}` : "")
                  }
                />
              ))}
            </div>
          </div>
        )}

        {/* Top processes */}
        <div className="space-y-2 pt-1">
          <h4 className="text-xs uppercase tracking-wider text-muted-foreground">
            Top processes (by CPU){!isHost && " — container only"}
          </h4>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-right w-16">PID</TableHead>
                <TableHead>Name</TableHead>
                <TableHead className="text-right">CPU %</TableHead>
                <TableHead className="text-right">Memory</TableHead>
                <TableHead className="text-right">Mem %</TableHead>
                <TableHead className="text-right">Threads</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {stats.top_processes.map((p) => (
                <TableRow key={p.pid} className={cn(p.is_self && "bg-secondary/40")}>
                  <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                    {p.pid}
                  </TableCell>
                  <TableCell className="font-medium text-sm">
                    {p.name}
                    {p.is_self && (
                      <span className="ml-2 text-[10px] text-muted-foreground">(backend)</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    <span className={cn(
                      p.cpu_percent > 50 && "text-amber-700",
                      p.cpu_percent > 80 && "text-red-700 font-semibold",
                    )}>
                      {p.cpu_percent.toFixed(1)}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-xs">
                    {formatBytes(p.memory_bytes)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                    {p.memory_percent.toFixed(1)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                    {p.threads}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

// Single horizontal bar with label, value, and a colored fill that turns
// amber at 80% and red at 90% — universal "uh oh" thresholds borrowed from
// the worker Good-% column for visual consistency.
function UsageBar({
  icon, label, percent, detail,
}: {
  icon?: React.ReactNode;
  label: string;
  percent: number;
  detail: string;
}) {
  const colorClass =
    percent >= 90 ? "bg-red-600" :
    percent >= 80 ? "bg-amber-500" :
    percent >= 60 ? "bg-blue-500" :
    "bg-emerald-500";
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between text-sm">
        <div className="flex items-center gap-1.5 font-medium">
          {icon}
          <span>{label}</span>
        </div>
        <span className={cn(
          "tabular-nums",
          percent >= 90 && "text-red-700 font-semibold",
          percent >= 80 && percent < 90 && "text-amber-700",
        )}>
          {percent.toFixed(1)}%
        </span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={cn("h-full transition-all", colorClass)}
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
      <p className="text-xs text-muted-foreground">{detail}</p>
    </div>
  );
}

// --------------------------------------------------------------------------
// Phase 12.6 — Out-of-range operator-limit warnings
//
// Each row tells the operator: this tag is *currently* outside its
// configured min/max. The view sorts worst-first so the most-deviating
// reading is on top — that's usually the one to investigate first.
// --------------------------------------------------------------------------

function OutOfRangeCard({ rows }: { rows: OutOfRangeTag[] }) {
  return (
    <Card className="border-amber-200">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base text-amber-800">
          <AlertOctagon className="h-4 w-4" />
          Operator limit warnings
          <Badge variant="outline" className="ml-auto border-amber-300 text-amber-800">
            {rows.length}
          </Badge>
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Tags whose current value is outside the configured min/max range. Worst-deviating first.
        </p>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Tag</TableHead>
              <TableHead>Device</TableHead>
              <TableHead className="text-right">Value</TableHead>
              <TableHead className="text-right">Min</TableHead>
              <TableHead className="text-right">Max</TableHead>
              <TableHead>Violation</TableHead>
              <TableHead className="text-xs text-muted-foreground">Reason</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.tag_id}>
                <TableCell className="font-medium">{r.tag_name}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{r.device_name}</TableCell>
                <TableCell className="text-right tabular-nums">
                  <span className="font-semibold text-amber-800">
                    {r.value_double != null ? formatNumber(r.value_double) : "—"}
                  </span>
                  {r.engineering_unit && (
                    <span className="text-xs text-muted-foreground ml-1">{r.engineering_unit}</span>
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                  {r.min_value != null ? formatNumber(r.min_value) : "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                  {r.max_value != null ? formatNumber(r.max_value) : "—"}
                </TableCell>
                <TableCell>
                  <Badge
                    variant="outline"
                    className={cn(
                      r.violation === "LOW"
                        ? "border-blue-300 text-blue-800"
                        : "border-red-300 text-red-800"
                    )}
                  >
                    {r.violation === "LOW" ? "↓ below min" : "↑ above max"}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {r.st_reason ?? "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Small formatting helpers used by the Phase 12.6 sections
// --------------------------------------------------------------------------

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  if (n < 1024 ** 4) return `${(n / 1024 ** 3).toFixed(2)} GB`;
  return `${(n / 1024 ** 4).toFixed(2)} TB`;
}

function formatUptime(sec: number): string {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  if (sec < 86400) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return `${h}h ${m}m`;
  }
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  return `${d}d ${h}h`;
}

function formatNumber(n: number): string {
  // Use the central formatter — same display rules everywhere.
  return formatFloat(n);
}


/**
 * Build a compact breakdown string for the "Config issues" MetricStrip
 * card hint. e.g. "5 overlap · 1 stale". Skips zero-counts so the line
 * stays tight.
 */
function breakdownLabel(s: DiagnosticsSummary): string {
  const parts: string[] = [];
  if (s.overlap_count > 0)         parts.push(`${s.overlap_count} overlap`);
  if (s.block_fit_issue_count > 0) parts.push(`${s.block_fit_issue_count} block-fit`);
  if (s.stale_tag_count > 0)       parts.push(`${s.stale_tag_count} stale`);
  return parts.join(" · ");
}
