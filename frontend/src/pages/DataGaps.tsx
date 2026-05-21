/**
 * Phase 6 (slice 5) — Data Gaps.
 *
 * Pick a tag and a time window. Calls the existing
 * /api/diagnostics/data-gaps/{tag_id} endpoint and shows:
 *   - summary stats (total gaps, total downtime, uptime %)  ← computed client-side
 *   - table of individual gap intervals
 *
 * Backend returns a flat `list[DataGap]` ordered longest-first; we
 * compute downtime/uptime from it on the client. Visual timeline is
 * intentionally deferred — the table answers the question "when was
 * data missing?" cleanly without an extra chart lib.
 */
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, Play } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { type LiveTag } from "@/types/api";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

type DataGap = {
  tag_id: number;
  gap_start: string;
  gap_end: string;
  gap_seconds: number;
};

const RANGES = [
  { label: "Last hour", sec: 3600 },
  { label: "Last 6 hours", sec: 6 * 3600 },
  { label: "Last 24 hours", sec: 24 * 3600 },
  { label: "Last 7 days", sec: 7 * 24 * 3600 },
];

type RunKey = { tagId: number; sinceSec: number; minGap: number; windowSec: number };

export default function DataGaps() {
  const [tagQuery, setTagQuery] = useState("");
  const [selectedTagId, setSelectedTagId] = useState<number | null>(null);
  const [rangeSec, setRangeSec] = useState(3600);
  const [minGapSec, setMinGapSec] = useState("10");
  const [runKey, setRunKey] = useState<RunKey | null>(null);

  const tags = useQuery({
    queryKey: ["live"],
    queryFn: () => api.get<LiveTag[]>("/live"),
    staleTime: 30_000,
  });

  const filteredTags = useMemo(() => {
    if (!tags.data) return [];
    const q = tagQuery.toLowerCase();
    // Cap at 200 — far more than the typical install needs, and the
    // container is `max-h-40 overflow-auto` so it scrolls cleanly.
    // The earlier 30-cap silently hid the rest, which was confusing.
    if (!q) return tags.data.slice(0, 200);
    return tags.data.filter((t) => t.tag_name.toLowerCase().includes(q)).slice(0, 200);
  }, [tags.data, tagQuery]);

  const totalTags = tags.data?.length ?? 0;
  const shownCount = filteredTags.length;

  const selectedTag = useMemo(
    () => tags.data?.find((t) => t.tag_id === selectedTagId) ?? null,
    [tags.data, selectedTagId],
  );

  const gaps = useQuery({
    enabled: runKey !== null,
    queryKey: ["data-gaps", runKey],
    queryFn: async () => {
      if (!runKey) throw new Error("no run");
      const since = new Date(Date.now() - runKey.sinceSec * 1000).toISOString();
      return api.get<DataGap[]>(
        `/diagnostics/data-gaps/${runKey.tagId}?since=${encodeURIComponent(since)}&min_gap_sec=${runKey.minGap}`,
      );
    },
  });

  function run() {
    if (!selectedTagId) return;
    setRunKey({
      tagId: selectedTagId,
      sinceSec: rangeSec,
      minGap: parseInt(minGapSec, 10) || 10,
      windowSec: rangeSec,
    });
  }

  return (
    <div className="space-y-4 max-w-5xl mx-auto">
      <PageHeader
        title="Data gaps"
        subtitle="Find intervals where a tag wasn't being sampled — debug connectivity, validate stale-detection thresholds, quantify uptime"
      />

      <SectionCard flush>
        <div className="p-4 space-y-3">
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor="tag-search">Tag</Label>
              <span className="text-xs text-muted-foreground tabular-nums">
                {tagQuery
                  ? `${shownCount} match${shownCount === 1 ? "" : "es"}`
                  : `${shownCount} of ${totalTags}${shownCount < totalTags ? " (refine search to narrow)" : ""}`}
              </span>
            </div>
            <div className="relative max-w-md">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                id="tag-search"
                type="text"
                placeholder="Search tags…"
                value={tagQuery}
                onChange={(e) => setTagQuery(e.target.value)}
                className="pl-8"
              />
            </div>
            <div className="border rounded-md max-h-72 overflow-auto">
              {filteredTags.map((t) => (
                <button
                  key={t.tag_id}
                  type="button"
                  onClick={() => setSelectedTagId(t.tag_id)}
                  className={cn(
                    "block w-full text-left px-3 py-1.5 text-sm hover:bg-secondary transition-colors",
                    selectedTagId === t.tag_id && "bg-secondary",
                  )}
                >
                  <span className="font-medium">{t.tag_name}</span>
                  <span className="text-xs text-muted-foreground ml-2">
                    {t.device_name} · {t.groups.join(", ") || "—"}
                  </span>
                </button>
              ))}
              {filteredTags.length === 0 && (
                <p className="px-3 py-2 text-xs text-muted-foreground">No tags match.</p>
              )}
            </div>
            {selectedTag && (
              <p className="text-xs text-muted-foreground mt-1">
                Selected: <span className="font-medium text-foreground">{selectedTag.tag_name}</span>
                {" · "}{selectedTag.device_name}
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="range">Time range</Label>
              <div className="flex flex-wrap gap-1">
                {RANGES.map((r) => (
                  <button
                    key={r.sec}
                    type="button"
                    onClick={() => setRangeSec(r.sec)}
                    className={cn(
                      "px-3 py-1 text-xs rounded-md border transition-colors",
                      rangeSec === r.sec
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
              <Label htmlFor="min_gap">Minimum gap (seconds)</Label>
              <Input
                id="min_gap"
                type="number"
                min="1"
                value={minGapSec}
                onChange={(e) => setMinGapSec(e.target.value)}
                className="max-w-[120px]"
              />
            </div>
          </div>

          <div>
            <Button onClick={run} disabled={!selectedTagId || gaps.isFetching}>
              <Play className="h-4 w-4 mr-1.5" />
              {gaps.isFetching ? "Finding gaps…" : "Find gaps"}
            </Button>
          </div>
        </div>
      </SectionCard>

      {gaps.data && runKey && <GapResults gaps={gaps.data} windowSec={runKey.windowSec} minGap={runKey.minGap} />}
      {gaps.isError && (
        <SectionCard>
          <div className="text-sm" style={{ color: "var(--status-error-on-soft)" }}>
            {gaps.error instanceof ApiError ? gaps.error.detail : String(gaps.error)}
          </div>
        </SectionCard>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------

function GapResults({ gaps, windowSec, minGap }: { gaps: DataGap[]; windowSec: number; minGap: number }) {
  const totalDowntime = gaps.reduce((sum, g) => sum + g.gap_seconds, 0);
  const uptimePct = windowSec > 0
    ? Math.max(0, Math.min(100, ((windowSec - totalDowntime) / windowSec) * 100))
    : 100;

  // Sort chronologically for the table view (server returns longest-first)
  const sorted = [...gaps].sort(
    (a, b) => new Date(a.gap_start).getTime() - new Date(b.gap_start).getTime(),
  );

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <StatCard label="Gaps found" value={gaps.length.toString()} />
        <StatCard label="Total downtime" value={formatDuration(totalDowntime)} />
        <StatCard
          label="Uptime"
          value={`${uptimePct.toFixed(2)}%`}
          hint={uptimePct >= 99.9 ? "excellent" : uptimePct >= 99 ? "good" : uptimePct >= 95 ? "watch" : "poor"}
        />
      </div>

      {sorted.length === 0 ? (
        <SectionCard>
          <div className="text-sm text-center py-2" style={{ color: "var(--text-secondary)" }}>
            No gaps found in this window above the minimum-gap threshold of {minGap}s.
          </div>
        </SectionCard>
      ) : (
        <SectionCard flush>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12 text-right">#</TableHead>
                <TableHead>Start</TableHead>
                <TableHead>End</TableHead>
                <TableHead className="text-right">Duration</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((g, i) => (
                <TableRow key={`${g.gap_start}-${i}`}>
                  <TableCell className="text-right tabular-nums text-xs" style={{ color: "var(--text-secondary)" }}>
                    {i + 1}
                  </TableCell>
                  <TableCell className="text-xs font-mono">{formatTimestamp(g.gap_start)}</TableCell>
                  <TableCell className="text-xs font-mono">{formatTimestamp(g.gap_end)}</TableCell>
                  <TableCell className="text-right tabular-nums text-xs">
                    {formatDuration(g.gap_seconds)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </SectionCard>
      )}
    </div>
  );
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <SectionCard>
      <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--text-secondary)" }}>{label}</div>
      <div className="text-[22px] font-semibold tabular-nums mt-1" style={{ letterSpacing: "-0.02em" }}>{value}</div>
      {hint && <div className="text-xs" style={{ color: "var(--text-secondary)" }}>{hint}</div>}
    </SectionCard>
  );
}

function formatDuration(sec: number): string {
  if (sec < 60) return `${sec.toFixed(1)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
