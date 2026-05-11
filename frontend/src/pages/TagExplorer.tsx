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
import { Search, Trash2, AlertCircle, Plus, Upload, Download } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { type LiveTag, type BulkResult } from "@/types/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Drawer } from "@/components/ui/drawer";
import { DeviceTabs } from "@/components/ui/device-tabs";
import { CsvImportContent, type ImportRowResult, exportCsv } from "@/components/ui/csv-import";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

type Device = { id: number; name: string };
type RegisterBlock = {
  id: number;
  device_id: number;
  device_name: string;
  name: string;
  function_code: number;
  start_address: number;
  count: number;
};

export default function TagExplorer() {
  const queryClient = useQueryClient();
  const [group, setGroup] = useState<string>("");
  const [deviceId, setDeviceId] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [selectedTagId, setSelectedTagId] = useState<number | null>(null);
  const [creatingTag, setCreatingTag] = useState(false);
  const [importing, setImporting] = useState(false);

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

      {/* Device tabs */}
      <DeviceTabs
        devices={devices.data ?? []}
        value={deviceId ? parseInt(deviceId, 10) : null}
        onChange={(id) => setDeviceId(id === null ? "" : String(id))}
        counts={countsByDevice}
      />

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
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((t) => (
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
                  <TableCell className="font-medium">{t.tag_name}</TableCell>
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
                    {formatValue(t.value_double, t.value_text, t.data_type)}
                  </TableCell>
                </TableRow>
              ))}
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
            defaultDeviceId={deviceId ? parseInt(deviceId, 10) : undefined}
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
            ]}
            requiredColumns={["name", "device_name", "data_type", "function_code", "address"]}
            templateCsv={
              "name,device_name,block_name,data_type,function_code,address,register_count,byte_order,engineering_unit,scale,offset,min_value,max_value,description\n" +
              "MyPressure,FLOWCOMP_001,FC001_HR_0_29,float32,3,0,2,ABCD,bar,1,0,,,Sample tag\n"
            }
            templateFilename="tags-template.csv"
            onImport={async (rows) => {
              // Build TagCreate bodies from CSV rows. Resolve device_name and
              // block_name against the loaded device/block lists.
              const deviceByName: Record<string, number> = {};
              devices.data?.forEach((d) => { deviceByName[d.name] = d.id; });
              const blockByName: Record<string, number> = {};
              blocks.data?.forEach((b) => { blockByName[b.name] = b.id; });

              const bodies = rows.map((row) => {
                const deviceId = deviceByName[row.device_name];
                const blockId = row.block_name ? blockByName[row.block_name] : null;
                const dt = row.data_type;
                const defaultRC =
                  dt === "int32" || dt === "uint32" || dt === "float32" ? 2 :
                  dt === "int64" || dt === "uint64" || dt === "float64" ? 4 :
                  1;
                return {
                  device_id: deviceId,
                  register_block_id: blockId ?? null,
                  name: row.name,
                  description: row.description || null,
                  data_type: dt,
                  byte_order: row.byte_order || "ABCD",
                  function_code: parseInt(row.function_code, 10),
                  address: parseInt(row.address, 10),
                  register_count: row.register_count ? parseInt(row.register_count, 10) : defaultRC,
                  engineering_unit: row.engineering_unit || null,
                  scale: row.scale ? parseFloat(row.scale) : 1.0,
                  offset: row.offset ? parseFloat(row.offset) : 0.0,
                  min_value: row.min_value ? parseFloat(row.min_value) : null,
                  max_value: row.max_value ? parseFloat(row.max_value) : null,
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

              const serverResults = await api.post<BulkResult[]>(
                "/tags/bulk",
                { tags: validBodies },
              );

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
  description: string;
  engineering_unit: string;
  scale: string;       // strings so we can validate numerics on submit
  offset: string;
  min_value: string;
  max_value: string;
  enabled: boolean;
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
    mutationFn: async (body: Record<string, unknown>) => {
      return api.patch(`/tags/${tag.tag_id}`, body);
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
    if (form.description !== original.description)
      body.description = form.description || null;
    if (form.engineering_unit !== original.engineering_unit)
      body.engineering_unit = form.engineering_unit || null;
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

    if (Object.keys(body).length === 0) {
      setServerError("No changes to save.");
      return;
    }
    update.mutate(body);
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
          <DT label="Byte order">{tag.byte_order}</DT>
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
          <Label htmlFor="description">Description</Label>
          <Input
            id="description"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="engineering_unit">Engineering unit</Label>
            <Input
              id="engineering_unit"
              value={form.engineering_unit}
              onChange={(e) => setForm({ ...form, engineering_unit: e.target.value })}
              placeholder="e.g. bar, kg/h"
            />
          </div>

          <div className="space-y-1.5 flex flex-col">
            <Label htmlFor="enabled">Enabled</Label>
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
            <Label htmlFor="scale">Scale</Label>
            <Input
              id="scale"
              type="number"
              step="any"
              value={form.scale}
              onChange={(e) => setForm({ ...form, scale: e.target.value })}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="offset">Offset</Label>
            <Input
              id="offset"
              type="number"
              step="any"
              value={form.offset}
              onChange={(e) => setForm({ ...form, offset: e.target.value })}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="min_value">Min value</Label>
            <Input
              id="min_value"
              type="number"
              step="any"
              value={form.min_value}
              onChange={(e) => setForm({ ...form, min_value: e.target.value })}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="max_value">Max value</Label>
            <Input
              id="max_value"
              type="number"
              step="any"
              value={form.max_value}
              onChange={(e) => setForm({ ...form, max_value: e.target.value })}
            />
          </div>
        </div>

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
    description: tag.description ?? "",
    engineering_unit: tag.engineering_unit ?? "",
    scale: String(tag.scale),
    offset: String(tag.offset),
    min_value: tag.min_value === null ? "" : String(tag.min_value),
    max_value: tag.max_value === null ? "" : String(tag.max_value),
    enabled: tag.enabled,
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

const BYTE_ORDERS = ["ABCD", "CDAB", "BADC", "DCBA"] as const;

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
  engineering_unit: string;
  scale: string;
  offset: string;
  min_value: string;
  max_value: string;
};

function NewTagPanel({
  devices,
  blocks,
  defaultDeviceId,
  onCreated,
}: {
  devices: Device[];
  blocks: RegisterBlock[];
  defaultDeviceId?: number;
  onCreated: () => void;
}) {
  const [form, setForm] = useState<NewTagForm>(() => ({
    device_id: defaultDeviceId ? String(defaultDeviceId) : (devices[0] ? String(devices[0].id) : ""),
    register_block_id: "",
    name: "",
    description: "",
    data_type: "float32",
    byte_order: "ABCD",
    function_code: "3",
    address: "0",
    register_count: "2",
    engineering_unit: "",
    scale: "1",
    offset: "0",
    min_value: "",
    max_value: "",
  }));
  const [serverError, setServerError] = useState<string | null>(null);

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
    });
  }

  // When the data_type changes, sync register_count to match the type's width.
  function handleDataTypeChange(dt: string) {
    setForm({
      ...form,
      data_type: dt,
      register_count: String(DEFAULT_REGISTER_COUNT[dt] ?? 1),
    });
  }

  const create = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        device_id: parseInt(form.device_id, 10),
        name: form.name,
        description: form.description || null,
        data_type: form.data_type,
        byte_order: form.byte_order,
        function_code: parseInt(form.function_code, 10),
        address: parseInt(form.address, 10),
        register_count: parseInt(form.register_count, 10),
        engineering_unit: form.engineering_unit || null,
        scale: parseFloat(form.scale),
        offset: parseFloat(form.offset),
        min_value: form.min_value === "" ? null : parseFloat(form.min_value),
        max_value: form.max_value === "" ? null : parseFloat(form.max_value),
      };
      // Only include register_block_id when a block is actually selected;
      // omitting it (undefined) gets serialized away by JSON.stringify, which
      // matches the Pydantic default of None.
      if (form.register_block_id) {
        body.register_block_id = parseInt(form.register_block_id, 10);
      }
      return api.post("/tags", body);
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
          <Label htmlFor="device">Device</Label>
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
          <Label htmlFor="block">Register block</Label>
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
        <Label htmlFor="name">Name</Label>
        <Input
          id="name"
          required
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="e.g. PressureInlet"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="description">Description</Label>
        <Input
          id="description"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="data_type">Data type</Label>
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
          <Label htmlFor="byte_order">Byte order</Label>
          <select
            id="byte_order"
            value={form.byte_order}
            onChange={(e) => setForm({ ...form, byte_order: e.target.value })}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            {BYTE_ORDERS.map((bo) => (
              <option key={bo} value={bo}>{bo}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="function_code">FC</Label>
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
          <Label htmlFor="address">Address</Label>
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
          <Label htmlFor="register_count">Reg. count</Label>
          <Input
            id="register_count"
            type="number"
            required
            min="1"
            max="4"
            value={form.register_count}
            onChange={(e) => setForm({ ...form, register_count: e.target.value })}
          />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="engineering_unit">Unit</Label>
          <Input
            id="engineering_unit"
            value={form.engineering_unit}
            onChange={(e) => setForm({ ...form, engineering_unit: e.target.value })}
            placeholder="bar, kg/h, °C…"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="scale">Scale</Label>
          <Input
            id="scale"
            type="number"
            step="any"
            value={form.scale}
            onChange={(e) => setForm({ ...form, scale: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="offset">Offset</Label>
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
          <Label htmlFor="min_value">Min value (optional)</Label>
          <Input
            id="min_value"
            type="number"
            step="any"
            value={form.min_value}
            onChange={(e) => setForm({ ...form, min_value: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="max_value">Max value (optional)</Label>
          <Input
            id="max_value"
            type="number"
            step="any"
            value={form.max_value}
            onChange={(e) => setForm({ ...form, max_value: e.target.value })}
          />
        </div>
      </div>

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
  ], filename);
}

function tagFilenameStamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

function formatValue(d: number | null, t: string | null, dataType: string): string {
  if (t !== null && t !== undefined) return t;
  if (d === null || d === undefined) return "—";
  if (dataType === "bool") return d ? "TRUE" : "FALSE";
  if (dataType.startsWith("int") || dataType.startsWith("uint")) return Math.trunc(d).toString();
  const abs = Math.abs(d);
  if (abs === 0) return "0";
  if (abs < 0.01) return d.toExponential(2);
  if (abs >= 1000) return d.toFixed(1);
  if (abs >= 10) return d.toFixed(2);
  return d.toFixed(3);
}
