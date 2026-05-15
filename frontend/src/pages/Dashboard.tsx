/**
 * Live Dashboard — Phase 6 with enhancements:
 *   - Device tabs at top (All / per-device)
 *   - View toggle: cards vs table
 *   - Filters: group, status (valid/suspect/invalid/no data), name search
 *   - Sparklines on cards and in table (from /api/live/sparklines, 10s refresh)
 *
 * Two queries: /api/live every 2s (current values), /api/live/sparklines
 * every 10s (downsampled history). Combined client-side by tag_id.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Search, LayoutGrid, Rows3 } from "lucide-react";
import { api } from "@/lib/api";
import { type LiveTag, type TagSparkline } from "@/types/api";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Sparkline } from "@/components/ui/sparkline";
import { DevicePicker } from "@/components/ui/device-picker";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { formatTagValue } from "@/lib/format";
import { useNamedSetMap, resolveNamedSet, type NamedSetMap } from "@/lib/named-set-resolve";

const REFRESH_MS = 2_000;
const SPARK_REFRESH_MS = 10_000;

type Device = { id: number; name: string };
type ViewMode = "cards" | "table";
type StatusFilter = "" | "valid" | "suspect" | "invalid" | "no_data";

export default function Dashboard() {
  const [group, setGroup] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("");
  const [search, setSearch] = useState<string>("");
  const [viewMode, setViewMode] = useState<ViewMode>("cards");
  const [activeDeviceId, setActiveDeviceId] = useState<number | null>(null);

  const tags = useQuery({
    queryKey: ["live"],
    queryFn: () => api.get<LiveTag[]>("/live"),
    refetchInterval: REFRESH_MS,
  });

  const groups = useQuery({
    queryKey: ["live", "groups"],
    queryFn: () => api.get<string[]>("/live/groups"),
    staleTime: 60_000,
  });

  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () => api.get<Device[]>("/devices"),
    staleTime: 60_000,
  });

  // Sparklines fetched separately on a slower cadence to keep the main
  // /api/live response lean.
  const sparklines = useQuery({
    queryKey: ["live", "sparklines"],
    queryFn: () => api.get<TagSparkline[]>("/live/sparklines"),
    refetchInterval: SPARK_REFRESH_MS,
  });

  const sparkByTag = useMemo(() => {
    const m: Record<number, TagSparkline["points"]> = {};
    sparklines.data?.forEach((s) => { m[s.tag_id] = s.points; });
    return m;
  }, [sparklines.data]);

  // Phase 8.3 — named-set resolver, shared across both card and table views
  const { map: namedSetMap } = useNamedSetMap();

  // Count tags per device for the DevicePicker counts badge
  const countsByDevice = useMemo(() => {
    const counts: Record<number | "all", number> = { all: tags.data?.length ?? 0 };
    tags.data?.forEach((t) => {
      counts[t.device_id] = (counts[t.device_id] ?? 0) + 1;
    });
    return counts;
  }, [tags.data]);

  // Phase 11 — per-device health (worst-tag-wins) for DevicePicker dots.
  const healthByDevice = useMemo(() => {
    const ST_READ_OK = 128;
    const STALE_SEC = 30;
    const h: Record<number, "good" | "stale" | "error" | "unknown"> = {};
    const rank = { error: 3, stale: 2, good: 1, unknown: 0 };
    tags.data?.forEach((t) => {
      let state: "good" | "stale" | "error" | "unknown" = "unknown";
      if (t.st !== null && t.age_seconds !== null) {
        if (t.st !== ST_READ_OK) state = "error";
        else if (t.age_seconds > STALE_SEC) state = "stale";
        else state = "good";
      }
      const prev = h[t.device_id];
      if (!prev || rank[state] > rank[prev]) h[t.device_id] = state;
    });
    return h;
  }, [tags.data]);

  const filtered = useMemo(() => {
    if (!tags.data) return [];
    const lowerSearch = search.toLowerCase();
    return tags.data.filter((t) => {
      if (activeDeviceId !== null && t.device_id !== activeDeviceId) return false;
      if (group && !t.groups.includes(group)) return false;
      if (lowerSearch && !t.tag_name.toLowerCase().includes(lowerSearch)) return false;
      if (statusFilter) {
        const cat = categorizeStatus(t.st);
        if (cat !== statusFilter) return false;
      }
      return true;
    });
  }, [tags.data, activeDeviceId, group, search, statusFilter]);

  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Live Dashboard</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Current values + recent trend. Values refresh every {REFRESH_MS / 1000}s,
            sparklines every {SPARK_REFRESH_MS / 1000}s.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <RefreshCw className={cn("h-3 w-3", tags.isFetching && "animate-spin")} />
          <span>live</span>
        </div>
      </div>

      {/* Device picker */}
      <div>
        <DevicePicker
          devices={devices.data ?? []}
          value={activeDeviceId}
          onChange={setActiveDeviceId}
          counts={countsByDevice}
          deviceHealth={healthByDevice}
        />
      </div>

      {/* Filter row */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <select
          value={group}
          onChange={(e) => setGroup(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="">All groups</option>
          {groups.data?.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>

        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="">All statuses</option>
          <option value="valid">Valid only</option>
          <option value="suspect">Suspect only</option>
          <option value="invalid">Invalid only</option>
          <option value="no_data">No data only</option>
        </select>

        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            type="text"
            placeholder="Search by tag name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>

        <span className="text-sm text-muted-foreground tabular-nums">
          {tags.data ? `${filtered.length} of ${tags.data.length}` : "Loading…"}
        </span>

        {/* View mode toggle */}
        <div className="flex rounded-md border border-input p-0.5 sm:ml-auto">
          <button
            type="button"
            onClick={() => setViewMode("cards")}
            title="Cards view"
            className={cn(
              "p-1.5 rounded transition-colors",
              viewMode === "cards" ? "bg-secondary" : "hover:bg-secondary/60",
            )}
          >
            <LayoutGrid className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setViewMode("table")}
            title="Table view"
            className={cn(
              "p-1.5 rounded transition-colors",
              viewMode === "table" ? "bg-secondary" : "hover:bg-secondary/60",
            )}
          >
            <Rows3 className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Content */}
      {tags.isLoading ? (
        <p className="text-sm text-muted-foreground text-center mt-12">Loading tags…</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center mt-12">
          No tags match the current filter.
        </p>
      ) : viewMode === "cards" ? (
        activeDeviceId === null ? (
          <GroupedCards tags={filtered} sparkByTag={sparkByTag} namedSetMap={namedSetMap} />
        ) : (
          <CardGrid tags={filtered} sparkByTag={sparkByTag} namedSetMap={namedSetMap} />
        )
      ) : (
        activeDeviceId === null ? (
          <GroupedTable tags={filtered} sparkByTag={sparkByTag} namedSetMap={namedSetMap} />
        ) : (
          <TagsTable tags={filtered} sparkByTag={sparkByTag} namedSetMap={namedSetMap} />
        )
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Phase 11 — Device grouping helpers.
//
// When "All devices" is selected, we render one section per device with a
// header strip ("FLOWCOMP_001 · 177 tags"), then that device's cards/rows.
// This keeps the visual model "each device is its own block of data" rather
// than mixing them in one undifferentiated grid where it's easy to lose
// track of which tag belongs to which physical instrument.
// --------------------------------------------------------------------------

function groupByDevice<T extends { device_id: number; device_name: string }>(
  items: T[],
): Array<{ deviceId: number; deviceName: string; items: T[] }> {
  const map = new Map<number, { deviceName: string; items: T[] }>();
  for (const item of items) {
    const g = map.get(item.device_id);
    if (g) g.items.push(item);
    else map.set(item.device_id, { deviceName: item.device_name, items: [item] });
  }
  // Sort by device name so the order is stable across renders.
  return Array.from(map.entries())
    .map(([id, g]) => ({ deviceId: id, deviceName: g.deviceName, items: g.items }))
    .sort((a, b) => a.deviceName.localeCompare(b.deviceName));
}

function DeviceSectionHeader({
  name, count,
}: { name: string; count: number }) {
  return (
    <div className="flex items-baseline gap-2 pb-1 pt-2 border-b border-border/40">
      <h3 className="text-sm font-semibold">{name}</h3>
      <span className="text-xs text-muted-foreground tabular-nums">
        {count} tag{count === 1 ? "" : "s"}
      </span>
    </div>
  );
}

function GroupedCards({
  tags, sparkByTag, namedSetMap,
}: {
  tags: LiveTag[];
  sparkByTag: Record<number, { time: string; value: number }[]>;
  namedSetMap: NamedSetMap;
}) {
  const groups = useMemo(() => groupByDevice(tags), [tags]);
  return (
    <div className="space-y-4">
      {groups.map((g) => (
        <section key={g.deviceId} className="space-y-2">
          <DeviceSectionHeader name={g.deviceName} count={g.items.length} />
          <CardGrid tags={g.items} sparkByTag={sparkByTag} namedSetMap={namedSetMap} />
        </section>
      ))}
    </div>
  );
}

function GroupedTable({
  tags, sparkByTag, namedSetMap,
}: {
  tags: LiveTag[];
  sparkByTag: Record<number, { time: string; value: number }[]>;
  namedSetMap: NamedSetMap;
}) {
  const groups = useMemo(() => groupByDevice(tags), [tags]);
  return (
    <div className="space-y-4">
      {groups.map((g) => (
        <section key={g.deviceId} className="space-y-2">
          <DeviceSectionHeader name={g.deviceName} count={g.items.length} />
          <TagsTable tags={g.items} sparkByTag={sparkByTag} namedSetMap={namedSetMap} />
        </section>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------
// Cards view
// --------------------------------------------------------------------------

function CardGrid({
  tags, sparkByTag, namedSetMap,
}: {
  tags: LiveTag[];
  sparkByTag: Record<number, { time: string; value: number }[]>;
  namedSetMap: NamedSetMap;
}) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
      {tags.map((tag) => (
        <TagCard
          key={tag.tag_id}
          tag={tag}
          points={sparkByTag[tag.tag_id] ?? []}
          namedSetMap={namedSetMap}
        />
      ))}
    </div>
  );
}

function TagCard({
  tag, points, namedSetMap,
}: {
  tag: LiveTag;
  points: { time: string; value: number }[];
  namedSetMap: NamedSetMap;
}) {
  const hasData = tag.time !== null;
  return (
    <Card className={cn(!hasData && "opacity-60")}>
      <CardContent className="p-3 space-y-2">
        <div className="text-xs text-muted-foreground truncate font-medium flex items-center gap-1" title={tag.tag_name}>
          <span className="truncate">{tag.tag_name}</span>
          {tag.is_heartbeat && (
            <span
              className="text-rose-500 shrink-0"
              title={`Heartbeat watch · stale after ${tag.heartbeat_max_stale_sec ?? "?"}s`}
            >
              ♥
            </span>
          )}
        </div>

        <div className="flex items-baseline gap-1">
          {(() => {
            const resolved = resolveNamedSet(
              namedSetMap,
              tag.named_set_id,
              tag.value_double === null ? null : Math.round(tag.value_double),
            );
            if (resolved) {
              return (
                <>
                  <span
                    className="text-lg font-semibold"
                    style={resolved.color ? { color: resolved.color } : undefined}
                  >
                    {resolved.text}
                  </span>
                  <span className="text-[10px] text-muted-foreground tabular-nums">
                    ({formatValue(tag.value_double, tag.value_text, tag.data_type)})
                  </span>
                </>
              );
            }
            return (
              <>
                <span className="text-xl font-bold tabular-nums">
                  {formatValue(tag.value_double, tag.value_text, tag.data_type)}
                </span>
                {tag.engineering_unit && (
                  <span className="text-xs text-muted-foreground">{tag.engineering_unit}</span>
                )}
              </>
            );
          })()}
        </div>

        {points.length > 0 && (
          <div className="text-foreground/70 -mx-1">
            <Sparkline points={points} width={140} height={26} />
          </div>
        )}

        {tag.groups.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {tag.groups.slice(0, 2).map((g) => (
              <span key={g} className="inline-flex items-center rounded bg-secondary px-1.5 py-0.5 text-[10px]">
                {g}
              </span>
            ))}
            {tag.groups.length > 2 && (
              <span className="text-[10px] text-muted-foreground">+{tag.groups.length - 2}</span>
            )}
          </div>
        )}

        <div className="flex items-center justify-between gap-1">
          <StatusBadge st={tag.st} />
          <span className="text-[10px] text-muted-foreground tabular-nums">{formatAge(tag.age_seconds)}</span>
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Table view
// --------------------------------------------------------------------------

function TagsTable({
  tags, sparkByTag, namedSetMap,
}: {
  tags: LiveTag[];
  sparkByTag: Record<number, { time: string; value: number }[]>;
  namedSetMap: NamedSetMap;
}) {
  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Device</TableHead>
              <TableHead>Groups</TableHead>
              <TableHead className="text-right">Value</TableHead>
              <TableHead>Unit</TableHead>
              <TableHead>Trend</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Age</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tags.map((tag) => (
              <TableRow key={tag.tag_id}>
                <TableCell className="font-medium">
                  <span className="inline-flex items-center gap-1.5">
                    {tag.tag_name}
                    {tag.is_heartbeat && (
                      <span
                        className="text-rose-500"
                        title={`Heartbeat watch · stale after ${tag.heartbeat_max_stale_sec ?? "?"}s`}
                      >
                        ♥
                      </span>
                    )}
                  </span>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{tag.device_name}</TableCell>
                <TableCell className="text-xs">
                  {tag.groups.slice(0, 2).join(", ")}
                  {tag.groups.length > 2 && ` +${tag.groups.length - 2}`}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {(() => {
                    const resolved = resolveNamedSet(
                      namedSetMap,
                      tag.named_set_id,
                      tag.value_double === null ? null : Math.round(tag.value_double),
                    );
                    if (resolved) {
                      return (
                        <span className="inline-flex items-center gap-1.5 justify-end">
                          <span
                            className="text-xs font-medium"
                            style={resolved.color ? { color: resolved.color } : undefined}
                          >
                            {resolved.text}
                          </span>
                          <span className="text-[10px] text-muted-foreground">
                            ({formatValue(tag.value_double, tag.value_text, tag.data_type)})
                          </span>
                        </span>
                      );
                    }
                    return formatValue(tag.value_double, tag.value_text, tag.data_type);
                  })()}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {tag.engineering_unit ?? "—"}
                </TableCell>
                <TableCell className="text-foreground/70">
                  <Sparkline points={sparkByTag[tag.tag_id] ?? []} width={90} height={22} />
                </TableCell>
                <TableCell><StatusBadge st={tag.st} /></TableCell>
                <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                  {formatAge(tag.age_seconds)}
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
// Shared helpers
// --------------------------------------------------------------------------

function categorizeStatus(st: number | null): StatusFilter {
  if (st === null || st === undefined) return "no_data";
  if (st >= 128) return "valid";
  if (st >= 64) return "suspect";
  return "invalid";
}

function StatusBadge({ st }: { st: number | null }) {
  if (st == null) return <Badge variant="outline" className="text-[10px]">no data</Badge>;
  if (st >= 128) return <Badge variant="success" className="text-[10px]">valid</Badge>;
  if (st >= 64) return <Badge variant="warning" className="text-[10px]">suspect</Badge>;
  return <Badge variant="destructive" className="text-[10px]">invalid</Badge>;
}

function formatValue(d: number | null, t: string | null, dataType: string): string {
  // Thin wrapper over the shared formatter; keeps the call sites tidy.
  return formatTagValue(d, t, dataType);
}

function formatAge(ageSec: number | null): string {
  if (ageSec == null) return "—";
  if (ageSec < 60) return `${ageSec.toFixed(0)}s ago`;
  if (ageSec < 3600) return `${(ageSec / 60).toFixed(0)}m ago`;
  return `${(ageSec / 3600).toFixed(1)}h ago`;
}
