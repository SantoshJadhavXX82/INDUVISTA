/**
 * Groups Master — CRUD UI for user-defined tag classifications.
 *
 * Groups are orthogonal to register_blocks: a tag belongs to one block
 * (Modbus polling unit) but can be in many groups (Area, Equipment, Report
 * snapshot set, etc.). This page manages the group definitions themselves;
 * membership is edited per-tag from the Tag Explorer drawer.
 *
 * Phase 8.2
 */
import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Pencil, Trash2, AlertCircle, Search, Folder } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Drawer } from "@/components/ui/drawer";
import { HelpTip } from "@/components/ui/help-tip";
import { api, ApiError } from "@/lib/api";
import { help } from "@/lib/help-text";
import { cn } from "@/lib/utils";
import type { Group, GroupCreate, GroupType } from "@/types/api";

const GROUP_TYPES: GroupType[] = ["AREA", "EQUIPMENT", "UNIT", "PACKAGE", "REPORT", "CUSTOM"];

const TYPE_LABELS: Record<GroupType, string> = {
  AREA: "Area",
  EQUIPMENT: "Equipment",
  UNIT: "Unit",
  PACKAGE: "Package",
  REPORT: "Report",
  CUSTOM: "Custom",
};

const TYPE_DESCRIPTIONS: Record<GroupType, string> = {
  AREA: "Plant or site location (e.g. North Plant, Refinery Unit 2)",
  EQUIPMENT: "Specific equipment (Compressor A, Pump P-101)",
  UNIT: "Process unit or section (Hydrogen Plant, Cooling Tower)",
  PACKAGE: "Packaged skid or system (Vendor skid, control package)",
  REPORT: "Tags grouped for a specific report or audit",
  CUSTOM: "Anything else — user-defined classification",
};

export default function Groups() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [showDisabled, setShowDisabled] = useState(true);
  const [filterType, setFilterType] = useState<string>("");
  const [createDrawerOpen, setCreateDrawerOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<Group | null>(null);

  const groupsQuery = useQuery<Group[]>({
    queryKey: ["groups", "all"],
    queryFn: () => api.get("/groups"),
    staleTime: 30_000,
  });

  // Filtered + grouped data for display
  const grouped = useMemo(() => {
    const all = groupsQuery.data ?? [];
    const q = search.trim().toLowerCase();
    const filtered = all.filter((g) => {
      if (!showDisabled && !g.enabled) return false;
      if (filterType && g.group_type !== filterType) return false;
      if (q) {
        const haystack = `${g.name} ${g.description ?? ""}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
    const groups = new Map<string, Group[]>();
    filtered.forEach((g) => {
      if (!groups.has(g.group_type)) groups.set(g.group_type, []);
      groups.get(g.group_type)!.push(g);
    });
    return GROUP_TYPES
      .filter((t) => groups.has(t))
      .map((t) => ({
        group_type: t,
        groups: groups.get(t)!.sort((a, b) => a.display_order - b.display_order || a.name.localeCompare(b.name)),
      }));
  }, [groupsQuery.data, search, filterType, showDisabled]);

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.patch(`/groups/${id}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups"] }),
  });

  const totalCount = groupsQuery.data?.length ?? 0;
  const enabledCount = (groupsQuery.data ?? []).filter((g) => g.enabled).length;
  const totalMembers = (groupsQuery.data ?? []).reduce((sum, g) => sum + g.in_use_count, 0);

  return (
    <div className="p-6 space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Groups</h1>
          <p className="text-sm text-muted-foreground mt-1">
            User-defined classifications for tags. Orthogonal to register
            blocks — a tag can belong to many groups (Area, Equipment, Report set, …).
          </p>
        </div>
        <Button onClick={() => setCreateDrawerOpen(true)} size="sm">
          <Plus className="h-4 w-4 mr-1.5" /> New group
        </Button>
      </header>

      <div className="grid grid-cols-3 gap-3">
        <Card>
          <CardContent className="pt-4">
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Total groups</div>
            <div className="text-2xl font-bold tabular-nums">{totalCount}</div>
            <div className="text-xs text-muted-foreground">{enabledCount} enabled</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Types</div>
            <div className="text-2xl font-bold tabular-nums">{grouped.length}</div>
            <div className="text-xs text-muted-foreground">distinct group types in use</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Total memberships</div>
            <div className="text-2xl font-bold tabular-nums">{totalMembers}</div>
            <div className="text-xs text-muted-foreground">Tag↔group links across all groups</div>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or description…"
            className="pl-7 h-9"
          />
        </div>
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="h-9 rounded-md border bg-background px-3 text-sm"
        >
          <option value="">All types</option>
          {GROUP_TYPES.map((t) => (
            <option key={t} value={t}>{TYPE_LABELS[t]}</option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={showDisabled}
            onChange={(e) => setShowDisabled(e.target.checked)}
            className="h-4 w-4"
          />
          Show disabled
        </label>
      </div>

      {/* Grouped table */}
      {groupsQuery.isLoading && (
        <Card><CardContent className="p-6 text-sm text-muted-foreground">Loading…</CardContent></Card>
      )}
      {grouped.length === 0 && !groupsQuery.isLoading && (
        <Card><CardContent className="p-6 text-sm text-muted-foreground text-center">
          {totalCount === 0
            ? "No groups yet. Click \"New group\" to create your first classification."
            : "No groups match your filters."}
        </CardContent></Card>
      )}
      {grouped.map(({ group_type, groups: typedGroups }) => (
        <Card key={group_type}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold text-muted-foreground">
              <Folder className="h-3.5 w-3.5 inline-block mr-1.5" />
              {TYPE_LABELS[group_type]}
              <span className="ml-2 text-xs font-normal">({typedGroups.length})</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[25%]">Name</TableHead>
                  <TableHead className="w-[35%]">Description</TableHead>
                  <TableHead className="w-[15%]">Parent</TableHead>
                  <TableHead className="text-right w-[8%]">Tags</TableHead>
                  <TableHead className="text-right w-[10%]">Status</TableHead>
                  <TableHead className="text-right w-[100px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {typedGroups.map((g) => (
                  <TableRow key={g.id} className={cn(!g.enabled && "opacity-60")}>
                    <TableCell className="font-medium">{g.name}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {g.description ?? <span className="italic">—</span>}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {g.parent_group_name ?? <span className="italic">—</span>}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <span className={cn(g.in_use_count > 0 && "font-semibold")}>{g.in_use_count}</span>
                    </TableCell>
                    <TableCell className="text-right">
                      <button
                        type="button"
                        onClick={() => toggleEnabled.mutate({ id: g.id, enabled: !g.enabled })}
                        className={cn(
                          "text-[10px] uppercase tracking-wide px-2 py-0.5 rounded",
                          g.enabled
                            ? "bg-green-100 text-green-800 hover:bg-green-200"
                            : "bg-secondary text-muted-foreground hover:bg-secondary/80",
                        )}
                        title="Click to toggle enabled"
                      >
                        {g.enabled ? "on" : "off"}
                      </button>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditingGroup(g)}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ))}

      <Drawer
        open={createDrawerOpen}
        onClose={() => setCreateDrawerOpen(false)}
        title="New group"
      >
        <GroupForm
          mode="create"
          allGroups={groupsQuery.data ?? []}
          onSaved={() => setCreateDrawerOpen(false)}
        />
      </Drawer>

      <Drawer
        open={!!editingGroup}
        onClose={() => setEditingGroup(null)}
        title={`Edit group: ${editingGroup?.name ?? ""}`}
      >
        {editingGroup && (
          <GroupForm
            mode="edit"
            group={editingGroup}
            allGroups={groupsQuery.data ?? []}
            onSaved={() => setEditingGroup(null)}
            onDeleted={() => setEditingGroup(null)}
          />
        )}
      </Drawer>
    </div>
  );
}

// --------------------------------------------------------------------------
// Shared create + edit form
// --------------------------------------------------------------------------
function GroupForm({
  mode,
  group,
  allGroups,
  onSaved,
  onDeleted,
}: {
  mode: "create" | "edit";
  group?: Group;
  allGroups: Group[];
  onSaved: () => void;
  onDeleted?: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<GroupCreate>({
    name: group?.name ?? "",
    description: group?.description ?? "",
    group_type: group?.group_type ?? "CUSTOM",
    parent_group_id: group?.parent_group_id ?? null,
    display_order: group?.display_order ?? 0,
    enabled: group?.enabled ?? true,
  });
  const [error, setError] = useState<string | null>(null);
  const [forceDelete, setForceDelete] = useState(false);

  // Parent group candidates: exclude self and disabled groups
  const parentCandidates = useMemo(
    () => allGroups.filter((g) => g.enabled && g.id !== group?.id),
    [allGroups, group?.id],
  );

  const save = useMutation({
    mutationFn: async (body: GroupCreate) => {
      const cleaned = {
        ...body,
        description: (body.description ?? "").toString().trim() || null,
        parent_group_id: body.parent_group_id || null,
      };
      return mode === "create"
        ? api.post<Group>("/groups", cleaned)
        : api.patch<Group>(`/groups/${group!.id}`, cleaned);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["groups"] });
      onSaved();
    },
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  const remove = useMutation({
    mutationFn: () =>
      api.delete(`/groups/${group!.id}${forceDelete ? "?force=true" : ""}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["groups"] });
      onDeleted?.();
    },
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!form.name.trim()) {
      setError("Name is required.");
      return;
    }
    save.mutate(form);
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="group-name">
          Name <span className="text-red-600">*</span>
          <HelpTip entry={help.group.name} />
        </Label>
        <Input
          id="group-name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="North Plant Compressors"
          autoFocus
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="group-type">
          Group type <HelpTip entry={help.group.group_type} />
        </Label>
        <select
          id="group-type"
          value={form.group_type}
          onChange={(e) => setForm({ ...form, group_type: e.target.value as GroupType })}
          className="w-full h-9 rounded-md border bg-background px-3 text-sm"
        >
          {GROUP_TYPES.map((t) => (
            <option key={t} value={t}>{TYPE_LABELS[t]}</option>
          ))}
        </select>
        <p className="text-xs text-muted-foreground">
          {TYPE_DESCRIPTIONS[form.group_type ?? "CUSTOM"]}
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="group-parent">
          Parent group (optional) <HelpTip entry={help.group.parent_group_id} />
        </Label>
        <select
          id="group-parent"
          value={form.parent_group_id ?? ""}
          onChange={(e) => setForm({
            ...form,
            parent_group_id: e.target.value ? parseInt(e.target.value, 10) : null,
          })}
          className="w-full h-9 rounded-md border bg-background px-3 text-sm"
        >
          <option value="">— None (top-level)</option>
          {parentCandidates.map((p) => (
            <option key={p.id} value={p.id}>
              [{TYPE_LABELS[p.group_type]}] {p.name}
            </option>
          ))}
        </select>
        <p className="text-xs text-muted-foreground">
          Used for nesting (e.g. "Compressor A" inside "North Plant").
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="group-desc">
          Description <HelpTip entry={help.group.description} />
        </Label>
        <Input
          id="group-desc"
          value={form.description ?? ""}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder="Optional notes"
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="group-order">
            Display order <HelpTip entry={help.group.display_order} />
          </Label>
          <Input
            id="group-order"
            type="number"
            value={form.display_order ?? 0}
            onChange={(e) => setForm({ ...form, display_order: parseInt(e.target.value, 10) || 0 })}
          />
          <p className="text-xs text-muted-foreground">Lower = first in lists.</p>
        </div>
        <div className="space-y-1.5 flex flex-col">
          <Label>
            Status <HelpTip entry={help.group.enabled} />
          </Label>
          <label className="flex items-center gap-2 h-9 text-sm">
            <input
              type="checkbox"
              checked={form.enabled ?? true}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              className="h-4 w-4"
            />
            <span className="text-muted-foreground">
              {form.enabled ? "enabled" : "disabled"}
            </span>
          </label>
        </div>
      </div>

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2 flex gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex gap-2 pt-2">
        <Button type="submit" disabled={save.isPending} className="flex-1">
          {save.isPending ? "Saving…" : mode === "create" ? "Create group" : "Save changes"}
        </Button>
      </div>

      {mode === "edit" && group && (
        <div className="pt-4 mt-4 border-t border-red-100 space-y-2">
          <h3 className="text-sm font-semibold text-red-700">Delete group</h3>
          <p className="text-xs text-muted-foreground">
            {group.in_use_count === 0
              ? "Group has no members. Safe to delete."
              : `${group.in_use_count} tag${group.in_use_count !== 1 ? "s are" : " is"} a member of this group. Deletion will remove the memberships (tags are kept).`}
          </p>
          {group.in_use_count > 0 && (
            <label className="flex items-center gap-2 text-xs text-red-700">
              <input
                type="checkbox"
                checked={forceDelete}
                onChange={(e) => setForceDelete(e.target.checked)}
                className="h-3.5 w-3.5"
              />
              I understand. Delete anyway and unlink all member tags.
            </label>
          )}
          <Button
            type="button"
            variant="outline"
            onClick={() => remove.mutate()}
            disabled={remove.isPending || (group.in_use_count > 0 && !forceDelete)}
            className="text-red-700 border-red-200 hover:bg-red-50"
          >
            <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            {remove.isPending ? "Deleting…" : "Delete group"}
          </Button>
        </div>
      )}
    </form>
  );
}
