/**
 * Phase 7 Batch 2 — Frame Inspector + Protocol Analyzer.
 *
 * Lets the engineer see the actual Modbus frames going out and coming back
 * for a chosen device. Capture is per-device and OFF by default so the
 * worker only does Valkey writes when someone's looking.
 *
 * The "Protocol Analyzer" piece (B2) is the stats panel + filters at the
 * top — all client-side aggregations over the most recent 200 frames.
 *
 * Polling at 1Hz when capture is on; paused when off (no point hammering
 * the API if there's nothing to read).
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, Play, Pause, AlertCircle, ArrowDown, ArrowUp,
  Zap, TrendingUp, AlertTriangle, RefreshCw, Square,
} from "lucide-react";
import { api } from "@/lib/api";
import { type Frame, type FramesResponse } from "@/types/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { FrameFields } from "@/components/forms/frame-fields";
import { cn } from "@/lib/utils";

type Device = {
  id: number;
  name: string;
  host: string;
  port: number;
  unit_id: number;
  channel_name?: string;
};

export default function FrameInspector() {
  const qc = useQueryClient();
  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(null);

  // Three-state capture mode:
  //   stopped — capture is OFF on backend, no UI polling, empty state shown
  //   running — capture is ON, UI polls every 1s, frames stream in
  //   paused  — capture stays ON (backend still collecting in Valkey),
  //             but UI stops polling so frames freeze for inspection
  type Mode = "stopped" | "running" | "paused";
  const [mode, setMode] = useState<Mode>("stopped");

  // Filters
  const [filterFc, setFilterFc] = useState<string>("all");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [slowThresholdMs, setSlowThresholdMs] = useState<string>("");

  // Devices for the selector
  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () => api.get<Device[]>("/devices"),
  });

  // Default-select first device on first load
  useEffect(() => {
    if (selectedDeviceId === null && devices.data && devices.data.length > 0) {
      setSelectedDeviceId(devices.data[0].id);
    }
  }, [devices.data, selectedDeviceId]);

  // Pull frames. Refetch ONLY while running — paused = freeze on screen.
  const framesQuery = useQuery({
    queryKey: ["frames", selectedDeviceId],
    queryFn: () => api.get<FramesResponse>(`/devices/${selectedDeviceId}/frames`),
    enabled: selectedDeviceId !== null,
    refetchInterval: () => (mode === "running" ? 1000 : false),
  });

  // Sync local mode with backend state on first load. If backend says
  // capture is on and we don't know yet, assume running. If backend says
  // off, ensure we're stopped.
  useEffect(() => {
    const enabled = framesQuery.data?.capture_enabled;
    if (enabled === undefined) return;
    if (enabled && mode === "stopped") setMode("running");
    if (!enabled && mode !== "stopped") setMode("stopped");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [framesQuery.data?.capture_enabled]);

  // Backend mutation: enable/disable capture
  const setCapture = useMutation({
    mutationFn: (enabled: boolean) =>
      api.post(`/devices/${selectedDeviceId}/frame-capture`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["frames", selectedDeviceId] }),
  });

  // Mode transitions
  const startOrResume = async () => {
    if (mode === "stopped") await setCapture.mutateAsync(true);
    setMode("running");
  };
  const pause = () => setMode("paused");
  const stop = async () => {
    await setCapture.mutateAsync(false);
    setMode("stopped");
  };

  // Apply filters to the raw frame list
  const filteredFrames = useMemo(() => {
    const raw = framesQuery.data?.frames ?? [];
    return raw.filter((f) => {
      if (filterFc !== "all" && f.function_code !== parseInt(filterFc, 10)) return false;
      if (errorsOnly && !f.error) return false;
      const slowMs = parseFloat(slowThresholdMs);
      if (!isNaN(slowMs) && (f.latency_ms ?? 0) < slowMs) return false;
      return true;
    });
  }, [framesQuery.data, filterFc, errorsOnly, slowThresholdMs]);

  // Stats — compute over the RX frames only (TX has no latency, no error
  // status in our model — that's all carried by the paired RX).
  const stats = useMemo(() => computeStats(filteredFrames), [filteredFrames]);

  const currentDevice = devices.data?.find((d) => d.id === selectedDeviceId);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Activity className="h-6 w-6" />
            Frame Inspector
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Live Modbus traffic capture and protocol analysis.
          </p>
        </div>
      </div>

      {/* Top controls: device picker + capture controls */}
      <Card>
        <CardContent className="pt-5 pb-4">
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Device
              </label>
              <select
                value={selectedDeviceId ?? ""}
                onChange={(e) => setSelectedDeviceId(parseInt(e.target.value, 10))}
                className="h-9 min-w-[220px] rounded-md border border-input bg-background px-3 text-sm"
              >
                {devices.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} — {d.host}:{d.port}
                  </option>
                ))}
              </select>
            </div>

            <div className="ml-auto flex items-center gap-3">
              <ModeBadge mode={mode} />

              <div className="flex items-center gap-1">
                {mode === "stopped" && (
                  <Button
                    onClick={startOrResume}
                    disabled={setCapture.isPending || selectedDeviceId === null}
                    size="sm"
                  >
                    <Play className="h-4 w-4 mr-1.5" />Start
                  </Button>
                )}
                {mode === "running" && (
                  <>
                    <Button onClick={pause} variant="outline" size="sm">
                      <Pause className="h-4 w-4 mr-1.5" />Pause
                    </Button>
                    <Button onClick={stop} variant="outline" size="sm"
                            disabled={setCapture.isPending}>
                      <Square className="h-4 w-4 mr-1.5" />Stop
                    </Button>
                  </>
                )}
                {mode === "paused" && (
                  <>
                    <Button onClick={startOrResume} size="sm">
                      <Play className="h-4 w-4 mr-1.5" />Resume
                    </Button>
                    <Button onClick={stop} variant="outline" size="sm"
                            disabled={setCapture.isPending}>
                      <Square className="h-4 w-4 mr-1.5" />Stop
                    </Button>
                  </>
                )}
              </div>
            </div>
          </div>

          {currentDevice && (
            <p className="text-xs text-muted-foreground mt-3">
              Unit ID {currentDevice.unit_id}
              {currentDevice.channel_name && <> · Channel {currentDevice.channel_name}</>}
              {mode === "running" && <> · Polling for new frames at 1Hz</>}
              {mode === "paused" && <> · UI paused — capture continues in background</>}
            </p>
          )}
        </CardContent>
      </Card>

      {/* B2 — Stats panel */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard
          icon={<Zap className="h-4 w-4" />}
          label="Frames"
          value={String(stats.totalFrames)}
          sub={`${stats.txCount} TX · ${stats.rxCount} RX`}
        />
        <StatCard
          icon={<RefreshCw className="h-4 w-4" />}
          label="Req/sec"
          value={stats.reqPerSec === null ? "—" : stats.reqPerSec.toFixed(1)}
          sub={`window ${stats.windowSec.toFixed(0)}s`}
        />
        <StatCard
          icon={<AlertTriangle className="h-4 w-4" />}
          label="Error %"
          value={stats.errorPct === null ? "—" : `${stats.errorPct.toFixed(1)}%`}
          sub={`${stats.errorCount} of ${stats.rxCount}`}
          tone={stats.errorPct !== null && stats.errorPct > 5 ? "warn" : "default"}
        />
        <StatCard
          icon={<TrendingUp className="h-4 w-4" />}
          label="Latency p50/p95"
          value={
            stats.p50 === null
              ? "—"
              : `${stats.p50.toFixed(1)} / ${stats.p95!.toFixed(1)} ms`
          }
          sub={stats.maxLatency === null ? "" : `max ${stats.maxLatency.toFixed(1)} ms`}
        />
        <StatCard
          icon={<Activity className="h-4 w-4" />}
          label="Slowest block"
          value={stats.slowestBlock?.name ?? "—"}
          sub={stats.slowestBlock ? `avg ${stats.slowestBlock.avgMs.toFixed(1)} ms` : ""}
        />
      </div>

      {/* Filter row */}
      <Card>
        <CardContent className="pt-4 pb-4">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-muted-foreground">FC:</label>
              <select
                value={filterFc}
                onChange={(e) => setFilterFc(e.target.value)}
                className="h-7 rounded-md border border-input bg-background px-2 text-xs"
              >
                <option value="all">All</option>
                <option value="1">FC1 — Read coils</option>
                <option value="2">FC2 — Read discrete inputs</option>
                <option value="3">FC3 — Read holding registers</option>
                <option value="4">FC4 — Read input registers</option>
              </select>
              <span className="text-[10px] text-muted-foreground italic">
                (reads only; writes captured separately when invoked)
              </span>
            </div>

            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={errorsOnly}
                onChange={(e) => setErrorsOnly(e.target.checked)}
              />
              <span>Errors only</span>
            </label>

            <div className="flex items-center gap-2">
              <label className="text-xs text-muted-foreground">Slow ≥</label>
              <input
                type="number"
                placeholder="ms"
                value={slowThresholdMs}
                onChange={(e) => setSlowThresholdMs(e.target.value)}
                className="h-7 w-16 rounded-md border border-input bg-background px-2 text-xs"
              />
              <span className="text-xs text-muted-foreground">ms</span>
            </div>

            <span className="ml-auto text-xs text-muted-foreground">
              {filteredFrames.length} of {framesQuery.data?.frames.length ?? 0} frames
            </span>
          </div>
        </CardContent>
      </Card>

      {/* Frame list */}
      <Card>
        <CardContent className="p-0">
          {mode === "stopped" && filteredFrames.length === 0 ? (
            <EmptyState reason="capture-off" />
          ) : mode === "running" && filteredFrames.length === 0 ? (
            <EmptyState reason="waiting" />
          ) : (
            <div className="divide-y">
              {mode === "paused" && (
                <div className="px-3 py-1.5 bg-amber-50 border-b border-amber-200 text-[11px] text-amber-900 flex items-center gap-2">
                  <Pause className="h-3 w-3" />
                  UI is paused. Backend is still capturing — click Resume to see new frames.
                </div>
              )}
              {filteredFrames.map((f, idx) => (
                <FrameRow key={`${f.transaction_id}-${f.direction}-${idx}`} frame={f} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------
// Mode badge
// --------------------------------------------------------------------------

function ModeBadge({ mode }: { mode: "stopped" | "running" | "paused" }) {
  if (mode === "running") {
    return (
      <Badge variant="success" className="font-mono text-xs gap-1">
        <span className="inline-block h-2 w-2 rounded-full bg-current animate-pulse" />
        CAPTURING
      </Badge>
    );
  }
  if (mode === "paused") {
    return (
      <Badge variant="warning" className="font-mono text-xs gap-1">
        <Pause className="h-3 w-3" />
        PAUSED
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="font-mono text-xs">
      IDLE
    </Badge>
  );
}

// --------------------------------------------------------------------------
// Stats
// --------------------------------------------------------------------------

type Stats = {
  totalFrames: number;
  txCount: number;
  rxCount: number;
  errorCount: number;
  errorPct: number | null;
  reqPerSec: number | null;
  windowSec: number;
  p50: number | null;
  p95: number | null;
  maxLatency: number | null;
  slowestBlock: { name: string; avgMs: number } | null;
};

function computeStats(frames: Frame[]): Stats {
  const tx = frames.filter((f) => f.direction === "tx");
  const rx = frames.filter((f) => f.direction === "rx");
  const errors = rx.filter((f) => f.error);
  const latencies = rx
    .map((f) => f.latency_ms)
    .filter((x): x is number => x !== null && !isNaN(x))
    .sort((a, b) => a - b);

  // Time window from oldest to newest TX
  let windowSec = 0;
  let reqPerSec: number | null = null;
  if (tx.length >= 2) {
    const newest = new Date(tx[0].timestamp).getTime();
    const oldest = new Date(tx[tx.length - 1].timestamp).getTime();
    windowSec = Math.max(1, (newest - oldest) / 1000);
    reqPerSec = tx.length / windowSec;
  }

  // Slowest block by average latency
  const byBlock = new Map<string, { sum: number; n: number }>();
  for (const f of rx) {
    if (f.latency_ms === null) continue;
    const acc = byBlock.get(f.block_name) ?? { sum: 0, n: 0 };
    acc.sum += f.latency_ms;
    acc.n += 1;
    byBlock.set(f.block_name, acc);
  }
  let slowestBlock: Stats["slowestBlock"] = null;
  for (const [name, { sum, n }] of byBlock) {
    const avg = sum / n;
    if (!slowestBlock || avg > slowestBlock.avgMs) {
      slowestBlock = { name, avgMs: avg };
    }
  }

  return {
    totalFrames: frames.length,
    txCount: tx.length,
    rxCount: rx.length,
    errorCount: errors.length,
    errorPct: rx.length > 0 ? (errors.length / rx.length) * 100 : null,
    reqPerSec,
    windowSec,
    p50: latencies.length ? latencies[Math.floor(latencies.length * 0.5)] : null,
    p95: latencies.length ? latencies[Math.floor(latencies.length * 0.95)] : null,
    maxLatency: latencies.length ? latencies[latencies.length - 1] : null,
    slowestBlock,
  };
}

// --------------------------------------------------------------------------
// Sub-components
// --------------------------------------------------------------------------

function StatCard({
  icon, label, value, sub, tone = "default",
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
  tone?: "default" | "warn";
}) {
  return (
    <Card>
      <CardContent className="pt-3.5 pb-3.5">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground mb-1">
          {icon}
          <span>{label}</span>
        </div>
        <div className={cn(
          "text-xl font-semibold tabular-nums",
          tone === "warn" && "text-amber-600",
        )}>
          {value}
        </div>
        {sub && <div className="text-[10px] text-muted-foreground mt-0.5 truncate">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function FrameRow({ frame }: { frame: Frame }) {
  const [expanded, setExpanded] = useState(false);
  const ts = new Date(frame.timestamp);
  const tsStr = ts.toLocaleTimeString("en-GB", {
    hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit",
  }) + "." + String(ts.getMilliseconds()).padStart(3, "0");

  return (
    <div
      className={cn(
        "px-3 py-2 hover:bg-secondary/30 cursor-pointer font-mono text-xs",
        frame.error && "bg-red-50/40",
      )}
      onClick={() => setExpanded((x) => !x)}
    >
      <div className="flex items-center gap-3">
        <span className="text-muted-foreground tabular-nums w-20">{tsStr}</span>
        <span className={cn(
          "inline-flex items-center gap-1 w-12 shrink-0",
          frame.direction === "tx" ? "text-blue-700" : "text-emerald-700",
        )}>
          {frame.direction === "tx"
            ? <><ArrowUp className="h-3 w-3" />TX</>
            : <><ArrowDown className="h-3 w-3" />RX</>}
        </span>
        <span className="w-12 shrink-0 text-foreground">FC{frame.function_code}</span>
        <span className="w-28 shrink-0 truncate text-muted-foreground" title={frame.block_name}>
          {frame.block_name}
        </span>
        <span className={cn(
          "flex-1 truncate",
          frame.error ? "text-red-700" : "text-muted-foreground",
        )}>
          {frame.summary}
        </span>
        {frame.latency_ms !== null && (
          <span className={cn(
            "tabular-nums w-16 text-right shrink-0",
            frame.latency_ms > 100 ? "text-amber-700 font-semibold" : "text-muted-foreground",
          )}>
            {frame.latency_ms.toFixed(1)} ms
          </span>
        )}
        <span className="text-muted-foreground w-12 text-right shrink-0">
          {frame.byte_count}B
        </span>
      </div>
      {expanded && (
        <div className="mt-2 pl-23 space-y-2 text-[11px] leading-relaxed">
          <div>
            <span className="text-muted-foreground">bytes: </span>
            <span className="break-all">{frame.hex_bytes}</span>
          </div>
          <FrameFields frame={frame} />
          {frame.error && (
            <div className="text-red-700">⚠ {frame.error}</div>
          )}
        </div>
      )}
    </div>
  );
}

function EmptyState({ reason }: { reason: "capture-off" | "waiting" }) {
  if (reason === "capture-off") {
    return (
      <div className="py-16 text-center text-sm text-muted-foreground">
        <Pause className="h-8 w-8 mx-auto mb-3 opacity-30" />
        <p>Capture is off. Click <strong>Start capture</strong> to record frames.</p>
        <p className="mt-1 text-xs">Captures the worker's polling traffic — no extra requests.</p>
      </div>
    );
  }
  return (
    <div className="py-16 text-center text-sm text-muted-foreground">
      <div className="inline-block animate-pulse">
        <Activity className="h-8 w-8 mx-auto mb-3 opacity-50" />
      </div>
      <p>Waiting for the worker's next poll cycle…</p>
    </div>
  );
}
