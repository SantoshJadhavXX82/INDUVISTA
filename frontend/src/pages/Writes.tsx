/**
 * Phase 8.5.1 — Write audit log, revamped.
 *
 * Three completion items compared to the Phase 8.5 v1:
 *   1. Device filter tabs at the top (shared <DeviceTabs>)
 *   2. Before → Requested → After columns (was: just Requested + Verify)
 *   3. Locale-aware timestamps via lib/format.ts
 *
 * "Before" is the value the worker most recently captured in
 * latest_tag_values at the moment the write was issued. "After" is the
 * verify read-back when verify=true (empty otherwise). When Before == After,
 * the row gets a subtle "no-change" marker — useful to spot when an operator
 * wrote the same value that was already there.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Search,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Terminal,
  Globe,
  ArrowRight,
  Equal,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from "@/components/ui/table";
import { formatDateTimeWithSeconds } from "@/lib/format";
import { DeviceTabs } from "@/components/ui/device-tabs";
import { type LiveTag } from "@/types/api";
import { cn } from "@/lib/utils";

type WriteEntry = {
  id: number;
  time: string;
  tag_id: number | null;
  tag_name: string;
  source: "cli" | "rest";
  user_label: string | null;
  function_code: number;
  address: number;
  requested_value: string;
  success: boolean;
  error: string | null;
  verify_value: string | null;
  latency_ms: number | null;
  value_before: string | null;
};

const SINCE_RANGES = [
  { label: "Last hour", sec: 3600 },
  { label: "Last 24 hours", sec: 24 * 3600 },
  { label: "Last 7 days", sec: 7 * 24 * 3600 },
  { label: "All time", sec: 0 },
];

export default function Writes() {
  const [sinceSec, setSinceSec] = useState(24 * 3600);
  const [sourceFilter, setSourceFilter] = useState<"" | "cli" | "rest">("");
  const [successOnly, setSuccessOnly] = useState(false);
  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(null);
  const [search, setSearch] = useState("");

  // Pull /live for the device list — gives us tag_id → device_id mapping
  // so we can build the device tab filter even when writes target deleted tags.
  const live = useQuery({
    queryKey: ["live"],
    queryFn: () => api.get<LiveTag[]>("/live"),
    staleTime: 30_000,
  });

  const writes = useQuery<WriteEntry[]>({
    queryKey: ["writes", sinceSec, sourceFilter, successOnly, selectedDeviceId],
    queryFn: () => {
      const params = new URLSearchParams();
      if (sinceSec > 0) {
        const since = new Date(Date.now() - sinceSec * 1000).toISOString();
        params.set("since", since);
      }
      if (sourceFilter) params.set("source", sourceFilter);
      if (successOnly) params.set("success_only", "true");
      if (selectedDeviceId !== null) {
        params.set("device_id", String(selectedDeviceId));
      }
      params.set("limit", "500");
      return api.get<WriteEntry[]>(`/writes?${params.toString()}`);
    },
    refetchInterval: 10_000,
    staleTime: 5_000,
  });

  // Build device list + counts. Two contributors: (a) writes in the current
  // filtered view, (b) tag→device map from /live (write_journal only stores
  // tag_name_snapshot, not device_id, so we resolve via the live cache).
  const { deviceList, deviceCounts } = useMemo(() => {
    const tagToDevice = new Map<number, { id: number; name: string }>();
    for (const t of live.data ?? []) {
      tagToDevice.set(t.tag_id, { id: t.device_id, name: t.device_name });
    }
    const acc = new Map<number, { name: string; count: number }>();
    for (const w of writes.data ?? []) {
      if (w.tag_id == null) continue;
      const dev = tagToDevice.get(w.tag_id);
      if (!dev) continue;
      const cur = acc.get(dev.id);
      if (cur) cur.count++;
      else acc.set(dev.id, { name: dev.name, count: 1 });
    }
    const list = Array.from(acc, ([id, { name }]) => ({ id, name })).sort(
      (a, b) => a.name.localeCompare(b.name),
    );
    const counts: Record<number | "all", number> = {
      all: writes.data?.length ?? 0,
    };
    for (const [id, { count }] of acc) counts[id] = count;
    return { deviceList: list, deviceCounts: counts };
  }, [writes.data, live.data]);

  const filtered = useMemo(() => {
    const all = writes.data ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter((w) =>
      w.tag_name.toLowerCase().includes(q) ||
      (w.user_label ?? "").toLowerCase().includes(q) ||
      String(w.address).includes(q),
    );
  }, [writes.data, search]);

  const stats = useMemo(() => {
    const all = writes.data ?? [];
    const total = all.length;
    const succeeded = all.filter((w) => w.success).length;
    const failed = total - succeeded;
    return { total, succeeded, failed };
  }, [writes.data]);

  return (
    <div className="p-6 space-y-4 max-w-7xl">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Write audit log</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Every write — CLI and REST — recorded with the value before (from
            the live data), the requested value, and the verified read-back.
            Failed writes are kept too so you can see what was attempted.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => writes.refetch()}
          disabled={writes.isFetching}
        >
          <RefreshCw className={cn("h-3.5 w-3.5 mr-1.5", writes.isFetching && "animate-spin")} />
          Refresh
        </Button>
      </header>

      <div className="grid grid-cols-3 gap-3">
        <StatCard label="Writes" value={stats.total.toString()} />
        <StatCard label="Succeeded" value={stats.succeeded.toString()} tone="ok" />
        <StatCard label="Failed" value={stats.failed.toString()} tone={stats.failed > 0 ? "warn" : "muted"} />
      </div>

      <Card>
        <CardContent className="p-3 space-y-3">
          {/* Device tabs row */}
          <DeviceTabs
            devices={deviceList}
            value={selectedDeviceId}
            onChange={setSelectedDeviceId}
            counts={deviceCounts}
          />

          {/* Filters row */}
          <div className="flex flex-wrap gap-3 items-end pt-2">
            <div className="space-y-1.5">
              <Label className="text-xs">Time range</Label>
              <div className="flex gap-1">
                {SINCE_RANGES.map((r) => (
                  <button
                    key={r.sec}
                    type="button"
                    onClick={() => setSinceSec(r.sec)}
                    className={cn(
                      "px-2 py-1 text-xs rounded-md border transition-colors",
                      sinceSec === r.sec
                        ? "bg-foreground text-background border-foreground"
                        : "border-input hover:bg-secondary",
                    )}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Source</Label>
              <div className="flex gap-1">
                {[
                  { v: "", label: "All" },
                  { v: "cli", label: "CLI" },
                  { v: "rest", label: "REST" },
                ].map((s) => (
                  <button
                    key={s.v}
                    type="button"
                    onClick={() => setSourceFilter(s.v as "" | "cli" | "rest")}
                    className={cn(
                      "px-2 py-1 text-xs rounded-md border transition-colors",
                      sourceFilter === s.v
                        ? "bg-foreground text-background border-foreground"
                        : "border-input hover:bg-secondary",
                    )}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={successOnly}
                onChange={(e) => setSuccessOnly(e.target.checked)}
                className="h-4 w-4"
              />
              Succeeded only
            </label>

            <div className="flex-1 max-w-md ml-auto">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  type="text"
                  placeholder="Search tag, user, or address…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-8 h-9"
                />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {writes.isLoading && (
        <Card><CardContent className="p-6 text-sm text-muted-foreground">Loading…</CardContent></Card>
      )}

      {!writes.isLoading && filtered.length === 0 && (
        <Card><CardContent className="p-6 text-sm text-muted-foreground text-center">
          No writes match these filters.
        </CardContent></Card>
      )}

      {filtered.length > 0 && (
        <Card>
          <CardContent className="p-0 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[170px]">Time</TableHead>
                  <TableHead>Tag</TableHead>
                  <TableHead className="text-right w-[80px]">Addr</TableHead>
                  <TableHead className="w-[280px]">Before → Requested → After</TableHead>
                  <TableHead className="w-[80px]">Source</TableHead>
                  <TableHead>User</TableHead>
                  <TableHead className="text-right w-[80px]">Latency</TableHead>
                  <TableHead className="text-center w-[50px]">OK?</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((w) => (
                  <TableRow key={w.id} className={cn(!w.success && "bg-red-50/50")}>
                    <TableCell className="text-xs font-mono tabular-nums">
                      {formatDateTimeWithSeconds(w.time)}
                    </TableCell>
                    <TableCell className="text-xs font-medium">
                      {w.tag_name}
                      {!w.tag_id && (
                        <span className="ml-1 text-[10px] text-muted-foreground italic">
                          (deleted)
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-xs font-mono">
                      {w.address}
                    </TableCell>
                    <TableCell>
                      <ValueTransition
                        before={w.value_before}
                        requested={w.requested_value}
                        after={w.verify_value}
                        success={w.success}
                      />
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wide">
                        {w.source === "cli" ? (
                          <Terminal className="h-3 w-3" />
                        ) : (
                          <Globe className="h-3 w-3" />
                        )}
                        {w.source}
                      </span>
                    </TableCell>
                    <TableCell className="text-xs">
                      {w.user_label ?? <span className="italic text-muted-foreground">—</span>}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                      {w.latency_ms != null ? `${w.latency_ms.toFixed(0)}ms` : "—"}
                    </TableCell>
                    <TableCell className="text-center">
                      {w.success ? (
                        <CheckCircle2 className="h-4 w-4 text-green-600 inline-block" />
                      ) : (
                        <span title={w.error ?? "unknown error"}>
                          <XCircle className="h-4 w-4 text-red-600 inline-block" />
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ValueTransition({
  before,
  requested,
  after,
  success,
}: {
  before: string | null;
  requested: string;
  after: string | null;
  success: boolean;
}) {
  // Detect "no-change" — operator wrote the same value already there.
  // Compare string-normalized to handle trailing-zero differences.
  const noChange =
    success &&
    before != null &&
    after != null &&
    normalize(before) === normalize(after);
  return (
    <div className="flex items-center gap-1.5 text-xs font-mono">
      <span className="text-muted-foreground" title="Value before write">
        {before ?? "—"}
      </span>
      <ArrowRight className="h-3 w-3 text-muted-foreground/60 shrink-0" />
      <span className="font-medium" title="Requested by user">
        {requested}
      </span>
      <ArrowRight className="h-3 w-3 text-muted-foreground/60 shrink-0" />
      <span
        className={cn(
          after != null ? "text-foreground" : "text-muted-foreground italic",
        )}
        title={after != null ? "Verified read-back" : "Verify was off"}
      >
        {after ?? "(no verify)"}
      </span>
      {noChange && (
        <span
          className="inline-flex items-center text-[10px] text-amber-700 ml-1"
          title="Before equals After — wrote the same value that was already there"
        >
          <Equal className="h-3 w-3" />
        </span>
      )}
    </div>
  );
}

function normalize(s: string): string {
  // Strip trailing zeros from "1.0000" → "1"; leave non-numeric alone.
  const n = parseFloat(s);
  if (!isNaN(n) && isFinite(n)) return String(n);
  return s.trim().toLowerCase();
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: "ok" | "warn" | "muted" }) {
  return (
    <Card>
      <CardContent className="p-3">
        <div className="text-xs text-muted-foreground uppercase tracking-wider">{label}</div>
        <div className={cn(
          "text-2xl font-bold tabular-nums mt-1",
          tone === "ok" && "text-green-700",
          tone === "warn" && "text-red-700",
          tone === "muted" && "text-muted-foreground",
        )}>
          {value}
        </div>
      </CardContent>
    </Card>
  );
}
