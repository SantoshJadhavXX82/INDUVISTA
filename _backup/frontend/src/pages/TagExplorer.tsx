/**
 * Phase 6 (slice 3) — Tag Explorer.
 *
 * Table of all tags with filters (group, device, name search). Click a
 * row to open a side-drawer with full details and inline edits for the
 * safe-to-change fields (description, engineering_unit, scale, offset,
 * min/max, enabled). Delete with type-the-name confirmation.
 *
 * Mutations use TanStack Query's useMutation with automatic cache
 * invalidation on success — the table refreshes after every edit.
 *
 * Phase 3.5 hot-reload means address-relevant edits propagate to the
 * worker within ~10 seconds, visibly on the Diagnostics view.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Search, Trash2, AlertCircle, Plus, Upload, Download } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { type LiveTag, type PairTagLive, type BulkResult } from "@/types/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Drawer } from "@/components/ui/drawer";
import { DevicePicker } from "@/components/ui/device-picker";
import { CsvImportContent, type ImportRowResult, exportCsv } from "@/components/ui/csv-import";
import { AddressHelper } from "@/components/forms/address-helper";
import { ByteOrderHelp } from "@/components/forms/byte-order-help";
import { TestReadPanel } from "@/components/forms/test-read-panel";
import { UnitSelect } from "@/components/forms/unit-select";
import { GroupSelect } from "@/components/forms/group-select";
import { NamedSetSelect } from "@/components/forms/named-set-select";
import { HelpTip } from "@/components/ui/help-tip";
import { help } from "@/lib/help-text";
import { useNamedSetMap, resolveNamedSet } from "@/lib/named-set-resolve";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { formatTagValue } from "@/lib/format";
import { TagQualityBadge } from "@/components/tags/tag-quality-badge";

type Device = { id: number; name: string };
type RegisterBlock = {
  id: number;
  device_id: number;
  device_name: string;
  name: string;
  function_code: number;
  start_address: number;
  count: number;
  // Phase 8.5.1 — used as default for new tags + to lock the Writable
  // checkbox when block is read-only
  writable: boolean;
  // Phase 9.1 — surfaced on the response so future tag-form logic can
  // hint "this tag is in an Enron block, addresses are logical". No UI
  // behaviour change in this slice; future slices (bit-of-INT) will read it.
  addressing_mode: "STANDARD" | "ENRON_HOLDING" | "ENRON_INPUT";
};

export default function TagExplorer() {
  const queryClient = useQueryClient();
  const [group, setGroup] = useState<string>("");
  const [deviceId, setDeviceId] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [selectedTagId, setSelectedTagId] = useState<number | null>(null);
  const [creatingTag, setCreatingTag] = useState(false);
  const [importing, setImporting] = useState(false);

  // Phase 12.3 — Tag Explorer view mode tab strip.
  //   'all'  → physical tags + pair tags interleaved (pair tags with PAIR badge)
  //   'pair' → pair tags only
  const [viewMode, setViewMode] = useState<"all" | "pair">("all");

  // Phase 7 C4 — Register Browser handoff. When the user clicks "Create tag"
  // from /registers we receive ?create_from=N&fc=X&byte_order=Y&device_id=Z
  // and auto-open the create drawer with the form pre-filled. We clear the
  // params after consuming so a manual refresh doesn't re-trigger.
  const [searchParams, setSearchParams] = useSearchParams();
  const seedFromUrl = useMemo(() => {
    const cf = searchParams.get("create_from");
    if (cf === null) return null;
    return {
      address: cf,
      function_code: searchParams.get("fc") ?? "3",
      byte_order: searchParams.get("byte_order") ?? "ABCD",
      device_id: searchParams.get("device_id") ?? "",
    };
  }, [searchParams]);

  useEffect(() => {
    if (seedFromUrl) {
      setCreatingTag(true);
      // Clear the params so refresh doesn't re-trigger this flow.
      setSearchParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedFromUrl !== null]);

  // Multi-select for bulk delete
  const [selectedTagIds, setSelectedTagIds] = useState<Set<number>>(new Set());
  const [confirmingBulkDelete, setConfirmingBulkDelete] = useState(false);
  const [bulkDeleteResults, setBulkDeleteResults] = useState<
    { tag_id: number; success: boolean; error?: string }[] | null
  >(null);
  const masterCheckboxRef = useRef<HTMLInputElement>(null);

  // We reuse /api/live because it has everything we need (joined groups,
  // current value, device name) and is already optimized server-side.
  const tags = useQuery({
    queryKey: ["live"],
    queryFn: () => api.get<LiveTag[]>("/live"),
    refetchInterval: 5_000,
  });

  // Phase 12.3 — pair tags (logical tags derived from duty/standby device
  // pairs). Same refresh cadence so values stay in sync.
  const pairTags = useQuery({
    queryKey: ["pair-tags", "live"],
    queryFn: () => api.get<PairTagLive[]>("/pair-tags/live"),
    refetchInterval: 5_000,
  });

  // Phase 8.3 — for resolving display_text alongside live values
  const { map: namedSetMap } = useNamedSetMap();

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

  const blocks = useQuery({
    queryKey: ["register-blocks"],
    queryFn: () => api.get<RegisterBlock[]>("/register-blocks"),
    staleTime: 60_000,
  });

  const filtered = useMemo(() => {
    if (!tags.data) return [];
    const lowerSearch = search.toLowerCase();
    const did = deviceId ? parseInt(deviceId, 10) : null;
    return tags.data.filter((t) => {
      if (did !== null && t.device_id !== did) return false;
      if (group && !t.groups.includes(group)) return false;
      if (lowerSearch && !t.tag_name.toLowerCase().includes(lowerSearch)) return false;
      return true;
    });
  }, [tags.data, deviceId, group, search]);

  const countsByDevice = useMemo(() => {
    const counts: Record<number | "all", number> = { all: tags.data?.length ?? 0 };
    tags.data?.forEach((t) => {
      counts[t.device_id] = (counts[t.device_id] ?? 0) + 1;
    });
    return counts;
  }, [tags.data]);

  // Phase 11 — Per-device health aggregation for the DevicePicker dots.
  // Rule: error > stale > good > unknown. Worst tag wins so any failing
  // tag flashes red at the device-picker level.
  const healthByDevice = useMemo(() => {
    const ST_READ_OK = 128;
    const STALE_SEC = 30;
    const h: Record<number, "good" | "stale" | "error" | "unknown"> = {};
    tags.data?.forEach((t) => {
      let state: "good" | "stale" | "error" | "unknown" = "unknown";
      if (t.st !== null && t.age_seconds !== null) {
        if (t.st !== ST_READ_OK) state = "error";
        else if (t.age_seconds > STALE_SEC) state = "stale";
        else state = "good";
      }
      const prev = h[t.device_id];
      // worst-wins ordering
      const rank = { error: 3, stale: 2, good: 1, unknown: 0 };
      if (!prev || rank[state] > rank[prev]) {
        h[t.device_id] = state;
      }
    });
    return h;
  }, [tags.data]);

  // Master checkbox indeterminate state — three-way: all/some/none selected
  useEffect(() => {
    if (!masterCheckboxRef.current) return;
    const total = filtered.length;
    const sel = selectedTagIds.size;
    masterCheckboxRef.current.indeterminate = sel > 0 && sel < total;
  }, [filtered.length, selectedTagIds.size]);

  function toggleTag(id: number) {
    setSelectedTagIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllVisible() {
    if (selectedTagIds.size === filtered.length && filtered.length > 0) {
      setSelectedTagIds(new Set());
    } else {
      setSelectedTagIds(new Set(filtered.map((t) => t.tag_id)));
    }
  }

  function clearSelection() {
    setSelectedTagIds(new Set());
  }

  const bulkDelete = useMutation({
    mutationFn: () =>
      api.post<{ tag_id: number; success: boolean; error?: string }[]>(
        "/tags/bulk-delete",
        { tag_ids: Array.from(selectedTagIds) },
      ),
    onSuccess: (results) => {
      setBulkDeleteResults(results);
      queryClient.invalidateQueries({ queryKey: ["live"] });
      // Keep failed-to-delete tags selected so the user can act on them
      const failedIds = new Set(results.filter((r) => !r.success).map((r) => r.tag_id));
      setSelectedTagIds(failedIds);
    },
  });

  const selectedTag = useMemo(
    () => filtered.find((t) => t.tag_id === selectedTagId) ?? tags.data?.find((t) => t.tag_id === selectedTagId),
    [filtered, tags.data, selectedTagId],
  );

  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Tag Explorer</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Browse, search, and edit the {tags.data?.length ?? "…"} tags. Click any row to view details and edit.
        </p>
      </div>

      {/* Phase 12.3 — view tab strip. "All Tags" interleaves pair tags
          and physical tags with a PAIR/PHYS badge; "Pair Tags" focuses on
          just the duty/standby logical tags. The Pair Tags tab is hidden
          when no pairs exist (avoids an empty-state surprise). */}
      <div className="flex gap-1 border-b -mb-2">
        <button
          type="button"
          onClick={() => setViewMode("all")}
          className={cn(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
            viewMode === "all"
              ? "border-foreground text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          All tags
          <span className="ml-2 text-xs text-muted-foreground tabular-nums">
            {(tags.data?.length ?? 0) + (pairTags.data?.length ?? 0)}
          </span>
        </button>
        {(pairTags.data?.length ?? 0) > 0 && (
          <button
            type="button"
            onClick={() => setViewMode("pair")}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              viewMode === "pair"
                ? "border-foreground text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            Pair tags
            <span className="ml-2 text-xs text-muted-foreground tabular-nums">
              {pairTags.data?.length ?? 0}
            </span>
          </button>
        )}
      </div>

      {/* Device picker — searchable combobox with health overview */}
      <div>
        <DevicePicker
          devices={devices.data ?? []}
          value={deviceId ? parseInt(deviceId, 10) : null}
          onChange={(id) => setDeviceId(id === null ? "" : String(id))}
          counts={countsByDevice}
          deviceHealth={healthByDevice}
        />
      </div>

      {/* Bulk action bar — visible only when at least one tag is selected */}
      {selectedTagIds.size > 0 && (
        <div className="flex items-center gap-3 p-3 rounded-md border bg-amber-50 border-amber-200">
          <span className="text-sm font-medium">
            {selectedTagIds.size} selected
          </span>
          <Button variant="outline" size="sm" onClick={clearSelection}>
            Clear
          </Button>
          <Button
            size="sm"
            onClick={() => setConfirmingBulkDelete(true)}
            className="bg-red-600 hover:bg-red-700 text-white"
          >
            <Trash2 className="h-4 w-4 mr-1.5" />
            Delete {selectedTagIds.size}
          </Button>
        </div>
      )}

      {/* Filters */}
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

        <span className="text-sm text-muted-foreground tabular-nums sm:ml-auto">
          {tags.data ? `${filtered.length} of ${tags.data.length}` : "Loading…"}
        </span>

        <Button size="sm" variant="outline" onClick={() => exportTags(filtered)}>
          <Download className="h-4 w-4 mr-1.5" />
          Export CSV
        </Button>

        <Button size="sm" variant="outline" onClick={() => setImporting(true)}>
          <Upload className="h-4 w-4 mr-1.5" />
          Import CSV
        </Button>

        <Button size="sm" onClick={() => setCreatingTag(true)}>
          <Plus className="h-4 w-4 mr-1.5" />
          Add tag
        </Button>
      </div>

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <input
                    ref={masterCheckboxRef}
                    type="checkbox"
                    className="h-4 w-4 cursor-pointer"
                    checked={filtered.length > 0 && selectedTagIds.size === filtered.length}
                    onChange={toggleAllVisible}
                    aria-label="Select all visible tags"
                  />
                </TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Groups</TableHead>
                <TableHead>Device</TableHead>
                <TableHead className="text-right">FC</TableHead>
                <TableHead className="text-right">Addr</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Unit</TableHead>
                <TableHead className="text-right">Current</TableHead>
                <TableHead>Quality</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(() => {
                /* Phase 11 — device grouping. When viewing all devices,
                   insert a sticky-ish header row before each device's tags
                   to make ownership visually obvious. With ~250 tags spread
                   across 5 devices, the alternative (one undifferentiated
                   list) makes it easy to lose track of which physical
                   instrument a tag belongs to. */

                /* Phase 12.3 — pair-tag row renderer. Pair tags are NOT
                   editable (they're a virtual view over two physical tags),
                   so the checkbox column is rendered as empty and clicks on
                   the row are no-ops. The Quality column shows the live
                   value's quality from the currently-active (duty) side. */
                const renderPairRow = (pt: PairTagLive) => {
                  const ST_READ_OK = 128;
                  const STALE_SEC = 30;
                  return (
                    <TableRow
                      key={`pair-${pt.pair_tag_id}`}
                      className="bg-blue-50/30 hover:bg-blue-50/50 cursor-default"
                    >
                      <TableCell />
                      <TableCell className="font-medium">
                        <span className="inline-flex items-center gap-1.5">
                          {pt.tag_name}
                          <span className="inline-flex items-center rounded bg-blue-100 text-blue-800 px-1.5 py-0.5 text-[9px] font-medium tracking-wider">
                            PAIR
                          </span>
                        </span>
                      </TableCell>
                      <TableCell />
                      <TableCell className="text-xs">
                        <span className="text-emerald-700 font-medium">
                          {pt.active_device_name ?? "—"}
                        </span>
                        {pt.active_device_name && (
                          <span className="ml-1 text-muted-foreground">(duty)</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-xs">{pt.function_code}</TableCell>
                      <TableCell className="text-right tabular-nums text-xs">{pt.address}</TableCell>
                      <TableCell className="text-xs">{pt.data_type}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {pt.engineering_unit ?? "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatValue(pt.value_double, pt.value_text, pt.data_type)}
                      </TableCell>
                      <TableCell>
                        <TagQualityBadge
                          st={pt.st}
                          st_reason={pt.st_reason}
                          age_seconds={pt.age_seconds}
                        />
                      </TableCell>
                    </TableRow>
                  );
                };

                const renderRow = (t: LiveTag) => (
                  <TableRow
                    key={t.tag_id}
                    onClick={() => setSelectedTagId(t.tag_id)}
                    className={cn(
                      "cursor-pointer",
                      selectedTagIds.has(t.tag_id) && "bg-secondary/40",
                    )}
                  >
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        className="h-4 w-4 cursor-pointer"
                        checked={selectedTagIds.has(t.tag_id)}
                        onChange={() => toggleTag(t.tag_id)}
                        aria-label={`Select ${t.tag_name}`}
                      />
                    </TableCell>
                    <TableCell className="font-medium">
                      <span className="inline-flex items-center gap-1.5">
                        {t.tag_name}
                        {t.is_heartbeat && (
                          <span
                            className="text-rose-500"
                            title={`Heartbeat watch · stale after ${t.heartbeat_max_stale_sec ?? "?"}s`}
                          >
                            ♥
                          </span>
                        )}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {t.groups.slice(0, 2).map((g) => (
                          <span
                            key={g}
                            className="inline-flex items-center rounded bg-secondary px-1.5 py-0.5 text-[10px]"
                          >
                            {g}
                          </span>
                        ))}
                        {t.groups.length > 2 && (
                          <span className="text-[10px] text-muted-foreground">
                            +{t.groups.length - 2}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{t.device_name}</TableCell>
                    <TableCell className="text-right tabular-nums text-xs">{t.function_code}</TableCell>
                    <TableCell className="text-right tabular-nums text-xs">{t.address}</TableCell>
                    <TableCell className="text-xs">{t.data_type}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {t.engineering_unit ?? "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {(() => {
                        const resolved = resolveNamedSet(
                          namedSetMap,
                          t.named_set_id,
                          t.value_double === null ? null : Math.round(t.value_double),
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
                              <span className="text-[10px] text-muted-foreground tabular-nums">
                                ({formatValue(t.value_double, t.value_text, t.data_type)})
                              </span>
                            </span>
                          );
                        }
                        return formatValue(t.value_double, t.value_text, t.data_type);
                      })()}
                    </TableCell>
                    <TableCell>
                      <TagQualityBadge
                        st={t.st}
                        st_reason={t.st_reason}
                        age_seconds={t.age_seconds}
                      />
                    </TableCell>
                  </TableRow>
                );

                if (deviceId !== "") {
                  // Specific device — flat layout.
                  // In 'pair' view show only pair tags involving this device;
                  // in 'all' view show pair tags first, then physical rows.
                  const did = parseInt(deviceId, 10);
                  const pairsForDevice = (pairTags.data ?? []).filter(
                    (pt) =>
                      pt.primary_device_id === did || pt.partner_device_id === did,
                  ).filter(
                    (pt) =>
                      !search || pt.tag_name.toLowerCase().includes(search.toLowerCase()),
                  );
                  if (viewMode === "pair") {
                    return pairsForDevice.map(renderPairRow);
                  }
                  return [
                    ...pairsForDevice.map(renderPairRow),
                    ...filtered.map(renderRow),
                  ];
                }

                // All devices selected.
                const allPairs = (pairTags.data ?? []).filter(
                  (pt) =>
                    !search || pt.tag_name.toLowerCase().includes(search.toLowerCase()),
                );

                // Group pair tags by pair (primary_device_id, partner_device_id)
                const pairMap = new Map<string, { label: string; rows: PairTagLive[] }>();
                for (const pt of allPairs) {
                  const key = `${pt.primary_device_id}-${pt.partner_device_id}`;
                  const label = `Pair: ${pt.primary_device_name} ⇄ ${pt.partner_device_name}`;
                  const g = pairMap.get(key);
                  if (g) g.rows.push(pt);
                  else pairMap.set(key, { label, rows: [pt] });
                }
                const pairGroups = Array.from(pairMap.entries())
                  .map(([k, g]) => ({ key: k, label: g.label, rows: g.rows }))
                  .sort((a, b) => a.label.localeCompare(b.label));

                const pairSection = pairGroups.flatMap((g) => [
                  <TableRow key={`pair-hdr-${g.key}`} className="bg-muted/30 hover:bg-muted/30">
                    <TableCell colSpan={10} className="py-1.5 text-xs font-semibold">
                      {g.label}
                      <span className="ml-2 font-normal text-muted-foreground tabular-nums">
                        {g.rows.length} pair tag{g.rows.length === 1 ? "" : "s"}
                      </span>
                    </TableCell>
                  </TableRow>,
                  ...g.rows.map(renderPairRow),
                ]);

                if (viewMode === "pair") {
                  // Pair-tags-only view.
                  return pairSection;
                }

                // All tags — group physical by device with header rows.
                const groupMap = new Map<number, { name: string; rows: LiveTag[] }>();
                for (const t of filtered) {
                  const g = groupMap.get(t.device_id);
                  if (g) g.rows.push(t);
                  else groupMap.set(t.device_id, { name: t.device_name, rows: [t] });
                }
                const groups = Array.from(groupMap.entries())
                  .map(([id, g]) => ({ id, name: g.name, rows: g.rows }))
                  .sort((a, b) => a.name.localeCompare(b.name));

                const physicalSection = groups.flatMap((g) => [
                  <TableRow key={`hdr-${g.id}`} className="bg-muted/30 hover:bg-muted/30">
                    <TableCell colSpan={10} className="py-1.5 text-xs font-semibold">
                      {g.name}
                      <span className="ml-2 font-normal text-muted-foreground tabular-nums">
                        {g.rows.length} tag{g.rows.length === 1 ? "" : "s"}
                      </span>
                    </TableCell>
                  </TableRow>,
                  ...g.rows.map(renderRow),
                ]);

                return [...pairSection, ...physicalSection];
              })()}
            </TableBody>
          </Table>
          {filtered.length === 0 && tags.data && (
            <p className="text-sm text-muted-foreground text-center py-8">
              No tags match the current filter.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Detail drawer (edit existing) */}
      <Drawer
        open={selectedTagId !== null}
        onClose={() => setSelectedTagId(null)}
        title={selectedTag?.tag_name ?? ""}
        size="lg"
      >
        {selectedTag && (
          <TagEditPanel
            key={selectedTag.tag_id}
            tag={selectedTag}
            onSaved={() => {
              queryClient.invalidateQueries({ queryKey: ["live"] });
            }}
            onDeleted={() => {
              queryClient.invalidateQueries({ queryKey: ["live"] });
              setSelectedTagId(null);
            }}
          />
        )}
      </Drawer>

      {/* Create-tag drawer */}
      <Drawer
        open={creatingTag}
        onClose={() => setCreatingTag(false)}
        title="New tag"
        size="lg"
      >
        {creatingTag && (
          <NewTagPanel
            devices={devices.data ?? []}
            blocks={blocks.data ?? []}
            defaultDeviceId={
              seedFromUrl?.device_id
                ? parseInt(seedFromUrl.device_id, 10)
                : deviceId ? parseInt(deviceId, 10) : undefined
            }
            seedAddress={seedFromUrl?.address}
            seedFunctionCode={seedFromUrl?.function_code}
            seedByteOrder={seedFromUrl?.byte_order}
            onCreated={() => {
              queryClient.invalidateQueries({ queryKey: ["live"] });
              setCreatingTag(false);
            }}
          />
        )}
      </Drawer>

      {/* CSV import drawer */}
      <Drawer
        open={importing}
        onClose={() => setImporting(false)}
        title="Import tags from CSV"
        size="lg"
      >
        {importing && (
          <CsvImportContent
            expectedColumns={[
              "name", "device_name", "block_name", "data_type",
              "function_code", "address", "register_count",
              "byte_order", "engineering_unit", "scale", "offset",
              "min_value", "max_value", "description",
              // Phase 8.2 / 8.3 — extended schema
              "groups", "named_set",
              "is_heartbeat", "heartbeat_max_stale_sec",
              // Phase 8.5.1 — writability flag (true/false). When the column
              // is missing or blank, the tag inherits from its block's
              // access setting: RW block → writable=true, RO block → false.
              "writable",
            ]}
            requiredColumns={["name", "device_name", "data_type", "function_code", "address"]}
            templateCsv={
              "name,device_name,block_name,data_type,function_code,address,register_count,byte_order,engineering_unit,scale,offset,min_value,max_value,description,groups,named_set,is_heartbeat,heartbeat_max_stale_sec,writable\n" +
              "MyPressure,FLOWCOMP_001,FC001_HR_0_29,float32,3,0,2,ABCD,bar,1,0,,,Sample tag,Area-A;PT-101,,false,,false\n" +
              "MotorState,FLOWCOMP_001,,uint16,3,200,1,ABCD,,1,0,,,Run/stop state,Motor-01,MOTOR_STATE,false,,true\n"
            }
            templateFilename="tags-template.csv"
            onImport={async (rows) => {
              // Build TagCreate bodies from CSV rows. Resolve device_name and
              // block_name against the loaded device/block lists.
              const deviceByName: Record<string, number> = {};
              devices.data?.forEach((d) => { deviceByName[d.name] = d.id; });
              const blockByName: Record<string, number> = {};
              // Phase 8.5.1 — also index block writability by name so the
              // import can inherit writable=block.writable when the CSV
              // column is blank.
              const blockWritableByName: Record<string, boolean> = {};
              blocks.data?.forEach((b) => {
                blockByName[b.name] = b.id;
                blockWritableByName[b.name] = b.writable;
              });

              // Phase 8.1 — resolve engineering_unit text against the master
              // (case-insensitive on code or label). Matched values become FK;
              // unmatched values stay as override text. Mirrors the migration
              // 0005 backfill logic so CSV import and direct DB seed agree.
              let unitByKey: Record<string, number> = {};
              try {
                const units = await api.get<{ id: number; code: string; label: string }[]>(
                  "/engineering-units",
                );
                units.forEach((u) => {
                  unitByKey[u.code.toLowerCase()] = u.id;
                  unitByKey[u.label.toLowerCase()] = u.id;
                });
              } catch {
                // If the units endpoint is unreachable, fall back to override-only
                // behavior so the import still succeeds.
                unitByKey = {};
              }

              // Phase 8.3 — resolve named_set names to FKs.
              let namedSetByName: Record<string, number> = {};
              try {
                const sets = await api.get<{ id: number; name: string }[]>("/named-sets");
                sets.forEach((s) => {
                  namedSetByName[s.name.toLowerCase()] = s.id;
                });
              } catch {
                namedSetByName = {};
              }

              // Phase 8.2 — resolve group names. Auto-create groups that
              // appear in the CSV but don't exist yet (type=CUSTOM). This is
              // the friendliest behavior for round-trip workflows: export
              // from one InduVista, import into another, get the same groups
              // without manual prep.
              const groupByName: Record<string, number> = {};
              try {
                const groups = await api.get<{ id: number; name: string }[]>("/groups");
                groups.forEach((g) => { groupByName[g.name.toLowerCase()] = g.id; });
              } catch { /* leave empty — auto-create below will be skipped */ }

              const allRequestedGroupNames = new Set<string>();
              rows.forEach((row) => {
                (row.groups ?? "").split(";").forEach((g) => {
                  const trimmed = g.trim();
                  if (trimmed) allRequestedGroupNames.add(trimmed);
                });
              });
              for (const gname of allRequestedGroupNames) {
                if (!groupByName[gname.toLowerCase()]) {
                  try {
                    const created = await api.post<{ id: number; name: string }>("/groups", {
                      name: gname,
                      group_type: "CUSTOM",
                      enabled: true,
                    });
                    groupByName[created.name.toLowerCase()] = created.id;
                  } catch {
                    // Couldn't create (probably a race or duplicate) — skip,
                    // the tag will fail to assign that group and the user
                    // will see it in the per-row results.
                  }
                }
              }

              const bodies = rows.map((row) => {
                const deviceId = deviceByName[row.device_name];
                const blockId = row.block_name ? blockByName[row.block_name] : null;
                const dt = row.data_type;
                const defaultRC =
                  dt === "int32" || dt === "uint32" || dt === "float32" ? 2 :
                  dt === "int64" || dt === "uint64" || dt === "float64" ? 4 :
                  1;

                // Resolve unit: try master first, fall back to text override
                const unitText = (row.engineering_unit ?? "").trim();
                const unitMatchId = unitText
                  ? unitByKey[unitText.toLowerCase()] ?? null
                  : null;

                // Resolve named_set by name (case-insensitive)
                const namedSetText = (row.named_set ?? "").trim();
                const namedSetId = namedSetText
                  ? namedSetByName[namedSetText.toLowerCase()] ?? null
                  : null;

                // Heartbeat fields
                const hbStr = (row.is_heartbeat ?? "").trim().toLowerCase();
                const isHeartbeat = hbStr === "true" || hbStr === "1" || hbStr === "yes";
                const hbStaleSec = row.heartbeat_max_stale_sec
                  ? parseInt(row.heartbeat_max_stale_sec, 10) || null
                  : null;

                // Phase 8.5.1 — writability resolution:
                //   1. FC 2 / FC 4 → always false (DB CHECK forbids true here)
                //   2. CSV column present and non-empty → use it verbatim
                //   3. CSV column empty AND block_name set AND block.writable → inherit true
                //   4. Otherwise → false (safe default)
                const fcInt = parseInt(row.function_code, 10);
                const writableRaw = (row.writable ?? "").trim().toLowerCase();
                let writable = false;
                if (fcInt === 1 || fcInt === 3) {
                  if (writableRaw !== "") {
                    writable = ["true", "1", "yes"].includes(writableRaw);
                  } else if (row.block_name && blockWritableByName[row.block_name]) {
                    // Empty column + writable parent block → inherit true.
                    // Matches the rule the user asked for: "by default if the
                    // block is enabled for Read/Write then Tag shall also be
                    // Writable unless configuration is made as not writable".
                    writable = true;
                  }
                }

                return {
                  device_id: deviceId,
                  register_block_id: blockId ?? null,
                  name: row.name,
                  description: row.description || null,
                  data_type: dt,
                  byte_order: row.byte_order || "ABCD",
                  function_code: fcInt,
                  address: parseInt(row.address, 10),
                  register_count: row.register_count ? parseInt(row.register_count, 10) : defaultRC,
                  engineering_unit_id: unitMatchId,
                  engineering_unit: unitMatchId ? null : (unitText || null),
                  scale: row.scale ? parseFloat(row.scale) : 1.0,
                  offset: row.offset ? parseFloat(row.offset) : 0.0,
                  min_value: row.min_value ? parseFloat(row.min_value) : null,
                  max_value: row.max_value ? parseFloat(row.max_value) : null,
                  named_set_id: namedSetId,
                  is_heartbeat: isHeartbeat,
                  heartbeat_max_stale_sec: hbStaleSec,
                  writable,
                  // Carry the resolved group ids alongside the body so we can
                  // PUT them after the bulk insert returns the new tag ids.
                  _groupIds: (row.groups ?? "")
                    .split(";")
                    .map((g) => g.trim())
                    .filter((g) => g.length > 0)
                    .map((g) => groupByName[g.toLowerCase()])
                    .filter((id): id is number => typeof id === "number"),
                };
              });

              // Catch any unresolved device names client-side so the user
              // sees a clear "row 5: device 'XYZ' not found" instead of a
              // 422 from the server.
              const preFlightResults: ImportRowResult[] = [];
              const validBodies: typeof bodies = [];
              const validIndexes: number[] = [];
              bodies.forEach((b, i) => {
                if (b.device_id === undefined) {
                  preFlightResults.push({
                    row: i,
                    success: false,
                    message: `device '${rows[i].device_name}' not found`,
                  });
                } else if (rows[i].block_name && b.register_block_id === undefined) {
                  preFlightResults.push({
                    row: i,
                    success: false,
                    message: `block '${rows[i].block_name}' not found`,
                  });
                } else {
                  validIndexes.push(i);
                  validBodies.push(b);
                }
              });

              if (validBodies.length === 0) {
                return preFlightResults;
              }

              // Strip the local-only _groupIds field before sending — the
              // backend would reject extra fields.
              const groupIdsByLocalIndex: Record<number, number[]> = {};
              const sendBodies = validBodies.map((b, j) => {
                const { _groupIds, ...rest } = b as typeof b & { _groupIds: number[] };
                if (_groupIds && _groupIds.length > 0) {
                  groupIdsByLocalIndex[j] = _groupIds;
                }
                return rest;
              });

              const serverResults = await api.post<BulkResult[]>(
                "/tags/bulk",
                { tags: sendBodies },
              );

              // Phase 8.2 — for every successfully-created tag with groups,
              // issue a PUT to set memberships. Sequential to keep error
              // attribution clean; the count is bounded by the CSV import size.
              for (let j = 0; j < serverResults.length; j++) {
                const sr = serverResults[j];
                const groupIds = groupIdsByLocalIndex[j];
                if (sr.tag_id && groupIds && groupIds.length > 0) {
                  try {
                    await api.put(`/tags/${sr.tag_id}/groups`, { group_ids: groupIds });
                  } catch (e) {
                    // Don't fail the whole row — annotate it but keep going.
                    sr.error = `tag created, but group assignment failed: ${(e as Error).message}`;
                  }
                }
              }

              // Map server results back to original CSV row indexes
              const merged: ImportRowResult[] = [...preFlightResults];
              serverResults.forEach((sr, j) => {
                merged.push({
                  row: validIndexes[j],
                  success: !sr.error,
                  message: sr.error ?? undefined,
                });
              });
              merged.sort((a, b) => a.row - b.row);

              queryClient.invalidateQueries({ queryKey: ["live"] });
              queryClient.invalidateQueries({ queryKey: ["groups"] });
              return merged;
            }}
          />
        )}
      </Drawer>

      {/* Bulk delete confirmation drawer */}
      <Drawer
        open={confirmingBulkDelete}
        onClose={() => {
          if (!bulkDelete.isPending) {
            setConfirmingBulkDelete(false);
            setBulkDeleteResults(null);
          }
        }}
        title={bulkDeleteResults ? "Delete results" : `Delete ${selectedTagIds.size} tags?`}
      >
        {bulkDeleteResults ? (
          <BulkDeleteResults
            results={bulkDeleteResults}
            tagsById={
              (tags.data ?? []).reduce<Record<number, string>>((acc, t) => {
                acc[t.tag_id] = t.tag_name;
                return acc;
              }, {})
            }
            onClose={() => {
              setConfirmingBulkDelete(false);
              setBulkDeleteResults(null);
            }}
          />
        ) : (
          <div className="space-y-4">
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900">
              <p className="font-medium mb-1">This can't be undone.</p>
              <p>
                Tags with historical data (already-collected samples) can't actually
                be deleted; they'll come back as errors with a "disable instead" hint.
                Only tags with zero samples will be removed.
              </p>
            </div>

            <ul className="text-xs max-h-48 overflow-auto rounded-md border p-2 space-y-0.5 font-mono">
              {Array.from(selectedTagIds).slice(0, 50).map((id) => {
                const t = tags.data?.find((x) => x.tag_id === id);
                return <li key={id}>{t?.tag_name ?? `tag ${id}`}</li>;
              })}
              {selectedTagIds.size > 50 && (
                <li className="text-muted-foreground italic">
                  … and {selectedTagIds.size - 50} more
                </li>
              )}
            </ul>

            <div className="flex gap-2">
              <Button
                onClick={() => bulkDelete.mutate()}
                disabled={bulkDelete.isPending}
                className="bg-red-600 hover:bg-red-700 text-white"
              >
                <Trash2 className="h-4 w-4 mr-1.5" />
                {bulkDelete.isPending ? "Deleting…" : `Delete ${selectedTagIds.size} tags`}
              </Button>
              <Button
                variant="outline"
                onClick={() => setConfirmingBulkDelete(false)}
                disabled={bulkDelete.isPending}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}

function BulkDeleteResults({
  results, tagsById, onClose,
}: {
  results: { tag_id: number; success: boolean; error?: string }[];
  tagsById: Record<number, string>;
  onClose: () => void;
}) {
  const successes = results.filter((r) => r.success);
  const failures = results.filter((r) => !r.success);
  return (
    <div className="space-y-4">
      <div className="rounded-md border p-3 bg-secondary/30">
        <p className="text-sm">
          <strong>{successes.length}</strong> of {results.length} deleted successfully.
        </p>
      </div>
      {failures.length > 0 && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 space-y-1">
          <p className="text-sm font-medium text-red-900">
            {failures.length} couldn't be deleted:
          </p>
          <ul className="text-xs space-y-0.5 text-red-900 max-h-48 overflow-auto">
            {failures.map((f) => (
              <li key={f.tag_id}>
                <span className="font-mono">{tagsById[f.tag_id] ?? f.tag_id}:</span>{" "}
                {f.error}
              </li>
            ))}
          </ul>
        </div>
      )}
      <Button onClick={onClose}>Close</Button>
    </div>
  );
}

// --------------------------------------------------------------------------
// Tag edit panel — rendered inside the drawer
// --------------------------------------------------------------------------

type EditableFields = {
  // Phase 11 — tag name editable. The integer tag_id is the immutable
  // identity for the historian; renaming only updates the human-readable
  // label, preserving all references.
  name: string;
  description: string;
  // Phase 8.1 — dual-source unit. Exactly one of these is set (or both null).
  engineering_unit_id: number | null;
  engineering_unit: string;        // override text
  scale: string;       // strings so we can validate numerics on submit
  offset: string;
  min_value: string;
  max_value: string;
  enabled: boolean;
  is_heartbeat: boolean;
  heartbeat_max_stale_sec: string;
  // Phase 8.2 — group memberships. Persisted via a separate endpoint
  // (PUT /api/tags/:id/groups), not the main PATCH body.
  group_ids: number[];
  // Phase 8.3 — optional named set FK
  named_set_id: number | null;
  // Phase 8.5.1 — explicit write opt-in
  writable: boolean;
};

function TagEditPanel({
  tag,
  onSaved,
  onDeleted,
}: {
  tag: LiveTag;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  // Local form state, seeded from the tag whenever the tag prop changes.
  const [form, setForm] = useState<EditableFields>(() => seedForm(tag));
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [serverError, setServerError] = useState<string | null>(null);

  // (Form state is keyed by tag_id at the parent; the panel remounts when
  // a different tag is selected, so seedForm runs again automatically.)

  const update = useMutation({
    mutationFn: async ({ body, groupsChanged }: {
      body: Record<string, unknown>;
      groupsChanged: boolean;
    }) => {
      // Issue tag PATCH if anything in the body changed
      if (Object.keys(body).length > 0) {
        await api.patch(`/tags/${tag.tag_id}`, body);
      }
      // Phase 8.2 — group memberships go via PUT /tags/:id/groups
      if (groupsChanged) {
        await api.put(`/tags/${tag.tag_id}/groups`, { group_ids: form.group_ids });
      }
    },
    onSuccess: () => {
      setServerError(null);
      onSaved();
    },
    onError: (e: Error) => {
      setServerError(e instanceof ApiError ? e.detail : e.message);
    },
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/tags/${tag.tag_id}`),
    onSuccess: () => {
      onDeleted();
    },
    onError: (e: Error) => {
      setServerError(e instanceof ApiError ? e.detail : e.message);
    },
  });

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setServerError(null);

    // Build a diff of changed fields only — avoid sending unchanged values.
    const original = seedForm(tag);
    const body: Record<string, unknown> = {};
    if (form.name !== original.name)
      body.name = form.name;
    if (form.description !== original.description)
      body.description = form.description || null;
    if (form.engineering_unit !== original.engineering_unit)
      body.engineering_unit = form.engineering_unit || null;
    if (form.engineering_unit_id !== original.engineering_unit_id)
      body.engineering_unit_id = form.engineering_unit_id;
    if (form.scale !== original.scale)
      body.scale = parseFloat(form.scale);
    if (form.offset !== original.offset)
      body.offset = parseFloat(form.offset);
    if (form.min_value !== original.min_value)
      body.min_value = form.min_value === "" ? null : parseFloat(form.min_value);
    if (form.max_value !== original.max_value)
      body.max_value = form.max_value === "" ? null : parseFloat(form.max_value);
    if (form.enabled !== original.enabled)
      body.enabled = form.enabled;
    if (form.is_heartbeat !== original.is_heartbeat)
      body.is_heartbeat = form.is_heartbeat;
    if (form.heartbeat_max_stale_sec !== original.heartbeat_max_stale_sec)
      body.heartbeat_max_stale_sec =
        form.heartbeat_max_stale_sec === "" ? null : parseInt(form.heartbeat_max_stale_sec, 10);
    if (form.named_set_id !== original.named_set_id)
      body.named_set_id = form.named_set_id;
    if (form.writable !== original.writable)
      body.writable = form.writable;

    // Phase 8.2 — group memberships persist via a separate endpoint.
    const groupsChanged = (
      form.group_ids.length !== original.group_ids.length ||
      form.group_ids.some((id, i) => id !== original.group_ids[i])
    );

    if (Object.keys(body).length === 0 && !groupsChanged) {
      setServerError("No changes to save.");
      return;
    }
    update.mutate({ body, groupsChanged });
  }

  return (
    <form onSubmit={handleSave} className="space-y-4">
      {/* Identity (read-only) */}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Identity</h3>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <DT label="Device">{tag.device_name}</DT>
          <DT label="Tag ID">{tag.tag_id}</DT>
          <DT label="Data type">{tag.data_type}</DT>
          <DT label="Byte order">{byteOrderLabelFor(tag.data_type, tag.byte_order)}</DT>
          <DT label="Function code">{tag.function_code}</DT>
          <DT label="Address">{tag.address}</DT>
          <DT label="Register count">{tag.register_count}</DT>
          <DT label="Groups">{tag.groups.join(", ") || "—"}</DT>
        </dl>
      </section>

      {/* Editable */}
      <section className="space-y-3 pt-2 border-t">
        <h3 className="text-sm font-semibold">Editable</h3>

        <div className="space-y-1.5">
          <Label htmlFor="name">
            Name
            <span className="text-xs text-muted-foreground ml-1">
              (renaming preserves history)
            </span>
          </Label>
          <Input
            id="name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
            minLength={1}
            maxLength={100}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="description">
            Description <HelpTip entry={help.tag.description} />
          </Label>
          <Input
            id="description"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="engineering_unit">
              Engineering unit <HelpTip entry={help.tag.engineering_unit} />
            </Label>
            <UnitSelect
              value={{
                engineering_unit_id: form.engineering_unit_id,
                engineering_unit: form.engineering_unit || null,
              }}
              onChange={(v) => setForm({
                ...form,
                engineering_unit_id: v.engineering_unit_id,
                engineering_unit: v.engineering_unit ?? "",
              })}
            />
          </div>

          <div className="space-y-1.5 flex flex-col">
            <Label htmlFor="enabled">
              Enabled <HelpTip entry={help.tag.enabled} />
            </Label>
            <label className="flex items-center gap-2 h-9 text-sm">
              <input
                id="enabled"
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                className="h-4 w-4"
              />
              <span className="text-muted-foreground">
                {form.enabled ? "polled" : "skipped"}
              </span>
            </label>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="scale">
              Scale <HelpTip entry={help.tag.scale} />
            </Label>
            <Input
              id="scale"
              type="number"
              step="any"
              value={form.scale}
              onChange={(e) => setForm({ ...form, scale: e.target.value })}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="offset">
              Offset <HelpTip entry={help.tag.offset} />
            </Label>
            <Input
              id="offset"
              type="number"
              step="any"
              value={form.offset}
              onChange={(e) => setForm({ ...form, offset: e.target.value })}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="min_value">
              Min value <HelpTip entry={help.tag.min_value} />
            </Label>
            <Input
              id="min_value"
              type="number"
              step="any"
              value={form.min_value}
              onChange={(e) => setForm({ ...form, min_value: e.target.value })}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="max_value">
              Max value <HelpTip entry={help.tag.max_value} />
            </Label>
            <Input
              id="max_value"
              type="number"
              step="any"
              value={form.max_value}
              onChange={(e) => setForm({ ...form, max_value: e.target.value })}
            />
          </div>
        </div>

        {/* Phase 8.2 — group memberships */}
        <div className="space-y-1.5">
          <Label>Groups <HelpTip entry={help.tag.groups} /></Label>
          <GroupSelect
            value={form.group_ids}
            onChange={(ids) => setForm({ ...form, group_ids: ids })}
          />
          <p className="text-xs text-muted-foreground">
            Logical classifications — orthogonal to the polling block. Create
            new groups inline or manage them under Configuration → Groups.
          </p>
        </div>

        {/* Phase 8.3 — named set (value→label translator) */}
        <div className="space-y-1.5">
          <Label htmlFor="named_set">
            Enumeration
            <HelpTip entry={help.tag.named_set} />
          </Label>
          <NamedSetSelect
            value={form.named_set_id}
            onChange={(id) => setForm({ ...form, named_set_id: id })}
            dataType={tag.data_type}
          />
          <p className="text-xs text-muted-foreground">
            Translates raw integer values to readable text in dashboards
            and reports. Optional — works only for bool/int tags.
          </p>
        </div>

        {/* Phase 7 E1a — heartbeat watch */}
        <div className="rounded-md border bg-secondary/30 p-3 space-y-2">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={form.is_heartbeat}
              onChange={(e) => setForm({ ...form, is_heartbeat: e.target.checked })}
              className="h-4 w-4"
            />
            <span className="font-medium inline-flex items-center gap-1">
              Heartbeat watch
              <HelpTip entry={help.tag.is_heartbeat} />
              {form.is_heartbeat && <span className="text-rose-500">♥</span>}
            </span>
            <span className="text-xs text-muted-foreground">
              Alarm if the value freezes for too long
            </span>
          </label>
          {form.is_heartbeat && (
            <div className="pl-6 grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="hb_stale">
                  Stale after (seconds) <HelpTip entry={help.tag.heartbeat_max_stale_sec} />
                </Label>
                <Input
                  id="hb_stale"
                  type="number"
                  min="1"
                  placeholder="e.g. 30"
                  value={form.heartbeat_max_stale_sec}
                  onChange={(e) =>
                    setForm({ ...form, heartbeat_max_stale_sec: e.target.value })
                  }
                />
              </div>
              <div className="text-xs text-muted-foreground self-end pb-2">
                Worker marks samples <code>HEARTBEAT_FROZEN</code> when the
                value hasn't changed for this many seconds.
              </div>
            </div>
          )}
        </div>

        {/* Phase 8.5.1 — writability opt-in.
            If the parent block is Read-only, lock this checkbox OFF —
            tags can't be writable when their block isn't. To allow
            writes, the block's Access has to flip first. */}
        {(tag.function_code === 1 || tag.function_code === 3) && (() => {
          const blockIsReadOnly =
            tag.register_block_id != null && tag.block_writable === false;
          return (
            <div className={cn(
              "rounded-md border p-3 space-y-2",
              blockIsReadOnly ? "bg-muted/40 border-muted" : "bg-secondary/30",
            )}>
              <label className={cn(
                "flex items-center gap-2 text-sm",
                blockIsReadOnly ? "cursor-not-allowed opacity-60" : "cursor-pointer",
              )}>
                <input
                  type="checkbox"
                  checked={form.writable}
                  disabled={blockIsReadOnly}
                  onChange={(e) => setForm({ ...form, writable: e.target.checked })}
                  className="h-4 w-4"
                />
                <span className="font-medium">Writable</span>
                <span className="text-xs text-muted-foreground">
                  Allow writes from Write Console / CLI / REST
                </span>
                {form.writable && !blockIsReadOnly && (
                  <span className="ml-auto text-[10px] uppercase tracking-wide text-green-700 font-semibold">
                    enabled
                  </span>
                )}
              </label>
              {blockIsReadOnly && (
                <p className="pl-6 text-xs text-muted-foreground">
                  The parent Register Block is Read-only — tags in it
                  cannot be written. Open Configuration → Register Blocks
                  and set its Access to <span className="font-mono">Read + Write</span> to unlock.
                </p>
              )}
            </div>
          );
        })()}

        {serverError && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex gap-2">
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{serverError}</span>
          </div>
        )}

        <div className="flex gap-2">
          <Button type="submit" disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </section>

      {/* Danger zone */}
      <section className="pt-4 border-t border-red-100">
        <h3 className="text-sm font-semibold text-red-700">Delete tag</h3>
        <p className="text-xs text-muted-foreground mt-1 mb-2">
          Tags with historical data can't be deleted — disable them instead. Type{" "}
          <code className="font-mono bg-secondary px-1 rounded">{tag.tag_name}</code> to confirm.
        </p>
        <div className="flex gap-2">
          <Input
            value={deleteConfirm}
            onChange={(e) => setDeleteConfirm(e.target.value)}
            placeholder={tag.tag_name}
            className="flex-1"
          />
          <Button
            type="button"
            variant="outline"
            disabled={deleteConfirm !== tag.tag_name || remove.isPending}
            onClick={() => remove.mutate()}
            className={cn(
              "border-red-200 text-red-700 hover:bg-red-50",
              deleteConfirm === tag.tag_name && "ring-1 ring-red-200",
            )}
          >
            <Trash2 className="h-4 w-4 mr-1.5" />
            {remove.isPending ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </section>
    </form>
  );
}

function DT({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground uppercase tracking-wider">{label}</dt>
      <dd className="font-medium mt-0.5">{children}</dd>
    </div>
  );
}

function seedForm(tag: LiveTag): EditableFields {
  return {
    name: tag.tag_name,
    description: tag.description ?? "",
    // Phase 8.1 — prefer FK, fall back to override; only one is set in DB at a time.
    engineering_unit_id: tag.engineering_unit_id,
    engineering_unit: tag.engineering_unit_override ?? "",
    scale: String(tag.scale),
    offset: String(tag.offset),
    min_value: tag.min_value === null ? "" : String(tag.min_value),
    max_value: tag.max_value === null ? "" : String(tag.max_value),
    enabled: tag.enabled,
    is_heartbeat: tag.is_heartbeat,
    heartbeat_max_stale_sec:
      tag.heartbeat_max_stale_sec === null ? "" : String(tag.heartbeat_max_stale_sec),
    group_ids: tag.group_ids ?? [],
    named_set_id: tag.named_set_id,
    writable: tag.writable,
  };
}

// --------------------------------------------------------------------------
// New tag panel — rendered inside the create-tag drawer
// --------------------------------------------------------------------------

const DATA_TYPES = [
  "int16", "uint16",
  "int32", "uint32",
  "int64", "uint64",
  "float32", "float64",
  "bool", "string",
] as const;

/**
 * Byte-order options as a function of the tag's data_type.
 *
 * The DB stores the canonical 4-letter codes (ABCD/CDAB/BADC/DCBA) because
 * those map cleanly onto the four register/byte permutations the decoder
 * understands regardless of width. The UI labels show data-type-appropriate
 * letter counts so a user looking at a float64 doesn't see "ABCD" — they see
 * "ABCDEFGH" which honestly reflects the 8 bytes involved.
 *
 *   1-register types (bool/int16/uint16):
 *     ABCD → "AB"        (no swap)
 *     BADC → "BA"        (byte swap inside the single register)
 *     CDAB / DCBA → not shown (word-swap is meaningless for 1 register)
 *
 *   2-register types (int32/uint32/float32):
 *     ABCD/CDAB/BADC/DCBA as is.
 *
 *   4-register types (int64/uint64/float64):
 *     ABCD → "ABCDEFGH"  (big-endian, natural register order)
 *     CDAB → "GHEFCDAB"  (word swap — registers reversed)
 *     BADC → "BADCFEHG"  (byte swap within registers, register order intact)
 *     DCBA → "HGFEDCBA"  (little-endian — full reverse)
 *
 * Friendly descriptions in parentheses help engineers who don't think in
 * raw permutation strings.
 */
type ByteOrderOption = {
  /** DB value — always one of the four canonical 4-letter codes */
  value: "ABCD" | "CDAB" | "BADC" | "DCBA";
  /** UI label, width-appropriate */
  label: string;
  /** Friendly description */
  hint: string;
};

function byteOrderOptionsFor(dataType: string): ByteOrderOption[] {
  // 1-register: only no-swap vs byte-swap are meaningful.
  if (["bool", "int16", "uint16"].includes(dataType)) {
    return [
      { value: "ABCD", label: "AB", hint: "no swap" },
      { value: "BADC", label: "BA", hint: "byte swap" },
    ];
  }
  // 4-register (64-bit) types — show 8-letter labels.
  if (["int64", "uint64", "float64"].includes(dataType)) {
    return [
      { value: "ABCD", label: "ABCDEFGH", hint: "big-endian" },
      { value: "DCBA", label: "HGFEDCBA", hint: "little-endian" },
      { value: "CDAB", label: "GHEFCDAB", hint: "word swap" },
      { value: "BADC", label: "BADCFEHG", hint: "byte swap" },
    ];
  }
  // 2-register types (int32 / uint32 / float32) — canonical 4-letter labels.
  return [
    { value: "ABCD", label: "ABCD", hint: "big-endian" },
    { value: "DCBA", label: "DCBA", hint: "little-endian" },
    { value: "CDAB", label: "CDAB", hint: "word swap" },
    { value: "BADC", label: "BADC", hint: "byte swap" },
  ];
}

/** Render the byte_order DB code as the display label for a given data_type. */
function byteOrderLabelFor(dataType: string, dbValue: string): string {
  const opt = byteOrderOptionsFor(dataType).find((o) => o.value === dbValue);
  return opt?.label ?? dbValue;
}

// register_count is determined by data_type — set sensible defaults
const DEFAULT_REGISTER_COUNT: Record<string, number> = {
  int16: 1, uint16: 1, bool: 1,
  int32: 2, uint32: 2, float32: 2,
  int64: 4, uint64: 4, float64: 4,
  string: 1,
};

type NewTagForm = {
  device_id: string;
  register_block_id: string;  // "" means no block (writable tag)
  name: string;
  description: string;
  data_type: string;
  byte_order: string;
  function_code: string;
  address: string;
  register_count: string;
  // Phase 8.1 — dual-source unit
  engineering_unit_id: number | null;
  engineering_unit: string;        // override text
  scale: string;
  offset: string;
  min_value: string;
  max_value: string;
  is_heartbeat: boolean;
  heartbeat_max_stale_sec: string;
  // Phase 8.2 — group memberships (persisted via separate PUT after create)
  group_ids: number[];
  // Phase 8.3 — optional named set FK
  named_set_id: number | null;
  // Phase 8.5.1 — explicit write opt-in. Hidden when area is DI/IR.
  writable: boolean;
};

function NewTagPanel({
  devices,
  blocks,
  defaultDeviceId,
  seedAddress,
  seedFunctionCode,
  seedByteOrder,
  onCreated,
}: {
  devices: Device[];
  blocks: RegisterBlock[];
  defaultDeviceId?: number;
  seedAddress?: string;
  seedFunctionCode?: string;
  seedByteOrder?: string;
  onCreated: () => void;
}) {
  const [form, setForm] = useState<NewTagForm>(() => ({
    device_id: defaultDeviceId ? String(defaultDeviceId) : (devices[0] ? String(devices[0].id) : ""),
    register_block_id: "",
    name: "",
    description: "",
    data_type: "float32",
    byte_order: seedByteOrder ?? "ABCD",
    function_code: seedFunctionCode ?? "3",
    address: seedAddress ?? "0",
    register_count: "2",
    engineering_unit_id: null,
    engineering_unit: "",
    scale: "1",
    offset: "0",
    min_value: "",
    max_value: "",
    is_heartbeat: false,
    heartbeat_max_stale_sec: "",
    group_ids: [],
    named_set_id: null,
    writable: false,
  }));
  const [serverError, setServerError] = useState<string | null>(null);
  // Phase 9.1.1+ — register_count is auto-derived from data_type. Power
  // users can flip this to manually override the value; the API still
  // rejects (data_type, register_count) mismatches inside Enron blocks.
  const [rcOverride, setRcOverride] = useState(false);

  // Filter blocks to the selected device — null option for writable tags.
  const deviceBlocks = useMemo(
    () => blocks.filter((b) => b.device_id === parseInt(form.device_id, 10)),
    [blocks, form.device_id],
  );

  // When the user picks a block, snap function_code + address into the
  // block's range — keeps validation happy without manual re-entry.
  function handleBlockChange(blockId: string) {
    if (!blockId) {
      setForm({ ...form, register_block_id: "" });
      return;
    }
    const b = blocks.find((x) => x.id === parseInt(blockId, 10));
    if (!b) {
      setForm({ ...form, register_block_id: blockId });
      return;
    }
    setForm({
      ...form,
      register_block_id: blockId,
      function_code: String(b.function_code),
      address: String(b.start_address),
      // Phase 8.5.1 — auto-default tag writability from the selected block:
      //   - Block writable + FC 1/3 → default tag writable=true (user can untick)
      //   - Block read-only → force writable=false (checkbox will be disabled)
      // FC 2/4 are always read-only by spec so writable stays false either way.
      writable: b.writable && (b.function_code === 1 || b.function_code === 3),
    });
  }

  // When the data_type changes, sync register_count to match the type's width
  // AND snap byte_order to a sensible default for that width — the option set
  // differs by type, so a value valid for float32 may not be the obvious
  // default for float64.
  function handleDataTypeChange(dt: string) {
    const newOptions = byteOrderOptionsFor(dt);
    const currentStillValid = newOptions.some((o) => o.value === form.byte_order);
    setForm({
      ...form,
      data_type: dt,
      register_count: String(DEFAULT_REGISTER_COUNT[dt] ?? 1),
      byte_order: currentStillValid ? form.byte_order : newOptions[0].value,
    });
  }

  const create = useMutation({
    mutationFn: async () => {
      const fcInt = parseInt(form.function_code, 10);
      // Phase 8.5.1 — writable only meaningful for FC 1/3; force false otherwise.
      // DB CHECK constraint would reject it anyway, but client-side
      // pre-check gives the user a cleaner error.
      const writable = (fcInt === 1 || fcInt === 3) ? form.writable : false;
      const body: Record<string, unknown> = {
        device_id: parseInt(form.device_id, 10),
        name: form.name,
        description: form.description || null,
        data_type: form.data_type,
        byte_order: form.byte_order,
        function_code: fcInt,
        address: parseInt(form.address, 10),
        register_count: parseInt(form.register_count, 10),
        engineering_unit_id: form.engineering_unit_id,
        engineering_unit: form.engineering_unit_id ? null : (form.engineering_unit || null),
        scale: parseFloat(form.scale),
        offset: parseFloat(form.offset),
        min_value: form.min_value === "" ? null : parseFloat(form.min_value),
        max_value: form.max_value === "" ? null : parseFloat(form.max_value),
        is_heartbeat: form.is_heartbeat,
        heartbeat_max_stale_sec:
          form.is_heartbeat && form.heartbeat_max_stale_sec !== ""
            ? parseInt(form.heartbeat_max_stale_sec, 10)
            : null,
        named_set_id: form.named_set_id,
        writable,
      };
      // Only include register_block_id when a block is actually selected;
      // omitting it (undefined) gets serialized away by JSON.stringify, which
      // matches the Pydantic default of None.
      if (form.register_block_id) {
        body.register_block_id = parseInt(form.register_block_id, 10);
      }
      const created = await api.post<{ id: number }>("/tags", body);
      // Phase 8.2 — group memberships live on a separate endpoint. Only call
      // it if the user actually picked any; otherwise skip the round trip.
      if (form.group_ids.length > 0) {
        await api.put(`/tags/${created.id}/groups`, { group_ids: form.group_ids });
      }
      return created;
    },
    onSuccess: onCreated,
    onError: (e: Error) => setServerError(e instanceof ApiError ? e.detail : e.message),
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setServerError(null);
        create.mutate();
      }}
      className="space-y-4"
    >
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="device">
            Device <HelpTip entry={help.device.name} />
          </Label>
          <select
            id="device"
            required
            value={form.device_id}
            onChange={(e) => setForm({ ...form, device_id: e.target.value, register_block_id: "" })}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            {devices.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="block">
            Register block <HelpTip entry={help.block.name} />
          </Label>
          <select
            id="block"
            value={form.register_block_id}
            onChange={(e) => handleBlockChange(e.target.value)}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="">(none — writable, not polled)</option>
            {deviceBlocks.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name} (FC{b.function_code} @ {b.start_address}/{b.count})
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="name">
          Name <HelpTip entry={help.tag.name} />
        </Label>
        <Input
          id="name"
          required
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="e.g. PressureInlet"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="description">
          Description <HelpTip entry={help.tag.description} />
        </Label>
        <Input
          id="description"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
      </div>

      {/* Phase 9.1.1 — when an Enron block is selected, surface the
          uniform-width constraint so users don't try mixing types. */}
      {(() => {
        const selectedBlock = form.register_block_id
          ? blocks.find((b) => String(b.id) === form.register_block_id)
          : null;
        if (!selectedBlock || selectedBlock.addressing_mode === "STANDARD") {
          return null;
        }
        return (
          <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
            <span className="font-semibold uppercase tracking-wide text-[10px]">
              Enron block
            </span>
            <p className="mt-1">
              All tags in <span className="font-mono">{selectedBlock.name}</span> must share
              the same data type (uniform width). Addresses are <strong>logical</strong> —
              one address per value. The API rejects mixed-width tags inside an Enron block.
            </p>
          </div>
        );
      })()}

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="data_type">
            Data type <HelpTip entry={help.tag.data_type} />
          </Label>
          <select
            id="data_type"
            value={form.data_type}
            onChange={(e) => handleDataTypeChange(e.target.value)}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            {DATA_TYPES.map((dt) => (
              <option key={dt} value={dt}>{dt}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="byte_order" className="inline-flex items-center">
            Byte order
            <HelpTip entry={help.tag.byte_order} />
            <ByteOrderHelp />
          </Label>
          <select
            id="byte_order"
            value={form.byte_order}
            onChange={(e) => setForm({ ...form, byte_order: e.target.value })}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            {byteOrderOptionsFor(form.data_type).map((bo) => (
              <option key={bo.value} value={bo.value}>
                {bo.label} ({bo.hint})
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="function_code">
            FC <HelpTip entry={help.tag.function_code} />
          </Label>
          <select
            id="function_code"
            value={form.function_code}
            onChange={(e) => setForm({ ...form, function_code: e.target.value })}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="1">1</option>
            <option value="2">2</option>
            <option value="3">3</option>
            <option value="4">4</option>
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="address">
            Address <HelpTip entry={help.tag.address} />
          </Label>
          <Input
            id="address"
            type="number"
            required
            min="0"
            max="65535"
            value={form.address}
            onChange={(e) => setForm({ ...form, address: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="register_count" className="flex items-center gap-2">
            <span>Reg. count <HelpTip entry={help.tag.register_count} /></span>
            {!rcOverride && (
              <span className="ml-auto inline-flex items-center gap-1 rounded bg-secondary/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                auto
              </span>
            )}
          </Label>
          <Input
            id="register_count"
            type="number"
            required
            min="1"
            max="4"
            value={form.register_count}
            disabled={!rcOverride}
            onChange={(e) => setForm({ ...form, register_count: e.target.value })}
          />
          {/* Show the override toggle only outside Enron blocks. Inside an
              Enron block the API rejects (data_type, register_count)
              mismatches anyway, so exposing the toggle would just lead to
              confusing 400 errors. */}
          {(() => {
            const selectedBlock = form.register_block_id
              ? blocks.find((b) => String(b.id) === form.register_block_id)
              : null;
            const isEnron =
              selectedBlock != null &&
              selectedBlock.addressing_mode !== "STANDARD";
            if (isEnron) {
              return (
                <p className="text-[11px] text-muted-foreground">
                  Locked to the data type's natural width (Enron blocks).
                </p>
              );
            }
            return (
              <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
                <input
                  type="checkbox"
                  className="h-3 w-3"
                  checked={rcOverride}
                  onChange={(e) => setRcOverride(e.target.checked)}
                />
                Override (advanced — usually leave auto)
              </label>
            );
          })()}
        </div>
      </div>

      {/* C1 — address translator (PDU ↔ Modicon) */}
      <AddressHelper
        address={form.address}
        functionCode={parseInt(form.function_code, 10) || 3}
        onConvert={(pdu, impliedFc) => {
          setForm({
            ...form,
            address: String(pdu),
            function_code: String(impliedFc),
          });
        }}
      />

      {/* C2 — Test-read panel (only useful when a device is selected) */}
      {form.device_id && (
        <TestReadPanel
          deviceId={parseInt(form.device_id, 10)}
          functionCode={form.function_code}
          address={form.address}
          registerCount={form.register_count}
          onPick={(dataType, byteOrder) => {
            // Apply the chosen combination to the form. Also sync register_count
            // to match the type's natural width.
            const rc = DEFAULT_REGISTER_COUNT[dataType] ?? parseInt(form.register_count, 10);
            setForm({
              ...form,
              data_type: dataType,
              byte_order: byteOrder,
              register_count: String(rc),
            });
          }}
        />
      )}

      <div className="space-y-1.5">
        <Label htmlFor="engineering_unit">
          Engineering unit <HelpTip entry={help.tag.engineering_unit} />
        </Label>
        <UnitSelect
          value={{
            engineering_unit_id: form.engineering_unit_id,
            engineering_unit: form.engineering_unit || null,
          }}
          onChange={(v) => setForm({
            ...form,
            engineering_unit_id: v.engineering_unit_id,
            engineering_unit: v.engineering_unit ?? "",
          })}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="scale">
            Scale <HelpTip entry={help.tag.scale} />
          </Label>
          <Input
            id="scale"
            type="number"
            step="any"
            value={form.scale}
            onChange={(e) => setForm({ ...form, scale: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="offset">
            Offset <HelpTip entry={help.tag.offset} />
          </Label>
          <Input
            id="offset"
            type="number"
            step="any"
            value={form.offset}
            onChange={(e) => setForm({ ...form, offset: e.target.value })}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="min_value">
            Min value (optional) <HelpTip entry={help.tag.min_value} />
          </Label>
          <Input
            id="min_value"
            type="number"
            step="any"
            value={form.min_value}
            onChange={(e) => setForm({ ...form, min_value: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="max_value">
            Max value (optional) <HelpTip entry={help.tag.max_value} />
          </Label>
          <Input
            id="max_value"
            type="number"
            step="any"
            value={form.max_value}
            onChange={(e) => setForm({ ...form, max_value: e.target.value })}
          />
        </div>
      </div>

      {/* Phase 8.2 — group memberships */}
      <div className="space-y-1.5">
        <Label>Groups <HelpTip entry={help.tag.groups} /></Label>
        <GroupSelect
          value={form.group_ids}
          onChange={(ids) => setForm({ ...form, group_ids: ids })}
        />
        <p className="text-xs text-muted-foreground">
          Logical classifications — orthogonal to the polling block. Create
          new groups inline or manage them under Configuration → Groups.
        </p>
      </div>

      {/* Phase 8.3 — named set (value→label translator) */}
      <div className="space-y-1.5">
        <Label htmlFor="new_named_set">
          Enumeration
          <HelpTip entry={help.tag.named_set} />
        </Label>
        <NamedSetSelect
          value={form.named_set_id}
          onChange={(id) => setForm({ ...form, named_set_id: id })}
          dataType={form.data_type}
        />
        <p className="text-xs text-muted-foreground">
          Translates raw integer values to readable text in dashboards
          and reports. Optional — works only for bool/int tags.
        </p>
      </div>

      {/* Phase 7 E1a — heartbeat watch */}
      <div className="rounded-md border bg-secondary/30 p-3 space-y-2">
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={form.is_heartbeat}
            onChange={(e) => setForm({ ...form, is_heartbeat: e.target.checked })}
            className="h-4 w-4"
          />
          <span className="font-medium inline-flex items-center gap-1">
            Heartbeat watch <HelpTip entry={help.tag.is_heartbeat} />
          </span>
          <span className="text-xs text-muted-foreground">
            Alarm if the value freezes for too long
          </span>
        </label>
        {form.is_heartbeat && (
          <div className="pl-6 grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="heartbeat_stale">
                Stale after (seconds) <HelpTip entry={help.tag.heartbeat_max_stale_sec} />
              </Label>
              <Input
                id="heartbeat_stale"
                type="number"
                min="1"
                placeholder="e.g. 30"
                value={form.heartbeat_max_stale_sec}
                onChange={(e) => setForm({ ...form, heartbeat_max_stale_sec: e.target.value })}
              />
            </div>
            <div className="text-xs text-muted-foreground self-end pb-2">
              The worker marks samples <code>HEARTBEAT_FROZEN</code> if the
              value hasn't changed in this many seconds.
            </div>
          </div>
        )}
      </div>

      {/* Phase 8.5.1 — writability opt-in.
          Three states:
            - No block selected (unblocked writable tag): user-controlled
            - Block selected and block.writable=true: user-controlled, defaulted ON
            - Block selected and block.writable=false: locked OFF, checkbox disabled */}
      {(form.function_code === "1" || form.function_code === "3") && (() => {
        const selectedBlock = form.register_block_id
          ? blocks.find((b) => String(b.id) === form.register_block_id)
          : null;
        const blockIsReadOnly = selectedBlock != null && !selectedBlock.writable;
        return (
          <div className={cn(
            "rounded-md border p-3 space-y-2",
            blockIsReadOnly ? "bg-muted/40 border-muted" : "bg-secondary/30",
          )}>
            <label className={cn(
              "flex items-center gap-2 text-sm",
              blockIsReadOnly ? "cursor-not-allowed opacity-60" : "cursor-pointer",
            )}>
              <input
                type="checkbox"
                checked={form.writable}
                disabled={blockIsReadOnly}
                onChange={(e) => setForm({ ...form, writable: e.target.checked })}
                className="h-4 w-4"
              />
              <span className="font-medium">Writable</span>
              <span className="text-xs text-muted-foreground">
                Allow writes to this tag from Write Console / CLI / REST
              </span>
              {form.writable && !blockIsReadOnly && (
                <span className="ml-auto text-[10px] uppercase tracking-wide text-green-700 font-semibold">
                  enabled
                </span>
              )}
            </label>
            {blockIsReadOnly && (
              <p className="pl-6 text-xs text-muted-foreground">
                The selected block <span className="font-mono">{selectedBlock!.name}</span> is
                Read-only. To make tags in this block writable, edit the
                block's Access to <span className="font-mono">Read + Write</span> first.
              </p>
            )}
            {!blockIsReadOnly && form.writable && form.register_block_id && (
              <p className="pl-6 text-xs text-green-700">
                Block is Read+Write — this tag will be writable.
              </p>
            )}
            {!blockIsReadOnly && form.writable && !form.register_block_id && (
              <p className="pl-6 text-xs text-muted-foreground">
                This tag has no register block — it's a standalone writable
                tag that won't be polled but can be written.
              </p>
            )}
          </div>
        );
      })()}

      {serverError && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{serverError}</span>
        </div>
      )}

      <div className="flex gap-2">
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create tag"}
        </Button>
      </div>
    </form>
  );
}

function exportTags(tags: LiveTag[]): void {
  const filename = `induvista-tags-${tagFilenameStamp()}.csv`;
  exportCsv<LiveTag>(tags, [
    { header: "name", value: (t) => t.tag_name },
    { header: "device_name", value: (t) => t.device_name },
    { header: "block_name", value: (t) => t.register_block_name },
    { header: "data_type", value: (t) => t.data_type },
    { header: "function_code", value: (t) => t.function_code },
    { header: "address", value: (t) => t.address },
    { header: "register_count", value: (t) => t.register_count },
    { header: "byte_order", value: (t) => t.byte_order },
    { header: "engineering_unit", value: (t) => t.engineering_unit },
    { header: "scale", value: (t) => t.scale },
    { header: "offset", value: (t) => t.offset },
    { header: "min_value", value: (t) => t.min_value },
    { header: "max_value", value: (t) => t.max_value },
    { header: "description", value: (t) => t.description },
    // Phase 8.2 — groups as semicolon-separated names. Comma would clash
    // with the CSV delimiter; semicolon is the standard escape in spreadsheets.
    { header: "groups", value: (t) => (t.groups ?? []).join(";") },
    // Phase 8.3 — named set by name (resolved server-side on import)
    { header: "named_set", value: (t) => t.named_set_name },
    // Phase 7 E1a — heartbeat metadata
    { header: "is_heartbeat", value: (t) => (t.is_heartbeat ? "true" : "false") },
    { header: "heartbeat_max_stale_sec", value: (t) => t.heartbeat_max_stale_sec },
    // Phase 8.5.1 — writable flag (round-trips with import)
    { header: "writable", value: (t) => (t.writable ? "true" : "false") },
  ], filename);
}

function tagFilenameStamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

function formatValue(d: number | null, t: string | null, dataType: string): string {
  // Thin wrapper over the shared formatter; keeps existing call sites tidy.
  return formatTagValue(d, t, dataType);
}
