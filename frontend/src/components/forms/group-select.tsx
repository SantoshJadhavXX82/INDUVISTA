/**
 * GroupSelect — multi-select group picker with inline "+ Add new" affordance.
 *
 * Renders selected groups as removable chips + an "Add group" button that
 * opens a dropdown of available groups. Includes a footer hatch to create
 * a brand-new group on the spot without leaving the tag drawer.
 *
 * Phase 8.2
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, X, Search, Check } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { Group, GroupCreate, GroupType } from "@/types/api";

const GROUP_TYPES: GroupType[] = ["AREA", "EQUIPMENT", "UNIT", "PACKAGE", "REPORT", "CUSTOM"];

const TYPE_LABELS: Record<GroupType, string> = {
  AREA: "Area", EQUIPMENT: "Equipment", UNIT: "Unit",
  PACKAGE: "Package", REPORT: "Report", CUSTOM: "Custom",
};

export function GroupSelect({
  value,
  onChange,
  disabled,
}: {
  value: number[];
  onChange: (groupIds: number[]) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [showAddNew, setShowAddNew] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setShowAddNew(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const groupsQuery = useQuery<Group[]>({
    queryKey: ["groups", "enabled"],
    queryFn: () => api.get("/groups?enabled=true"),
    staleTime: 30_000,
  });

  const allGroups = groupsQuery.data ?? [];
  const selectedGroups = useMemo(
    () => allGroups.filter((g) => value.includes(g.id)),
    [allGroups, value],
  );

  // Available (not yet selected) groups, filtered + grouped by type
  const availableGrouped = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = allGroups
      .filter((g) => !value.includes(g.id))
      .filter((g) => {
        if (!q) return true;
        return (
          g.name.toLowerCase().includes(q) ||
          (g.description ?? "").toLowerCase().includes(q) ||
          g.group_type.toLowerCase().includes(q)
        );
      });
    const groups = new Map<GroupType, Group[]>();
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
  }, [allGroups, value, search]);

  function addGroup(g: Group) {
    onChange([...value, g.id]);
    setSearch("");
  }

  function removeGroup(id: number) {
    onChange(value.filter((v) => v !== id));
  }

  return (
    <div ref={containerRef} className="relative space-y-2">
      {/* Selected chips */}
      <div className={cn(
        "min-h-[36px] border rounded-md p-2 bg-background flex items-center gap-1.5 flex-wrap",
        disabled && "opacity-60",
      )}>
        {selectedGroups.length === 0 && (
          <span className="text-xs text-muted-foreground">No groups assigned</span>
        )}
        {selectedGroups.map((g) => (
          <span
            key={g.id}
            className="inline-flex items-center gap-1 bg-secondary text-secondary-foreground text-xs px-2 py-0.5 rounded"
          >
            <span className="text-[9px] uppercase tracking-wide opacity-60 mr-0.5">
              {TYPE_LABELS[g.group_type]}
            </span>
            <span>{g.name}</span>
            {!disabled && (
              <button
                type="button"
                onClick={() => removeGroup(g.id)}
                className="ml-1 text-muted-foreground hover:text-foreground"
                title="Remove from group"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </span>
        ))}
        {!disabled && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className={cn(
              "inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded",
              "border border-dashed border-foreground/30 text-muted-foreground hover:text-foreground hover:border-foreground/60",
            )}
          >
            <Plus className="h-3 w-3" /> Add group
          </button>
        )}
      </div>

      {open && (
        <div className="absolute z-50 mt-1 left-0 right-0 max-h-96 overflow-hidden rounded-md border bg-popover shadow-md flex flex-col">
          <div className="p-2 border-b">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search groups…"
                className="pl-7 h-8 text-sm"
                autoFocus
              />
            </div>
          </div>

          <div className="flex-1 overflow-auto py-1">
            {groupsQuery.isLoading && (
              <div className="px-3 py-2 text-xs text-muted-foreground">Loading…</div>
            )}
            {!groupsQuery.isLoading && availableGrouped.length === 0 && allGroups.length > 0 && (
              <div className="px-3 py-2 text-xs text-muted-foreground">
                {search ? `No groups match "${search}"` : "All available groups already assigned."}
              </div>
            )}
            {!groupsQuery.isLoading && allGroups.length === 0 && (
              <div className="px-3 py-2 text-xs text-muted-foreground">
                No groups yet — create your first below.
              </div>
            )}
            {availableGrouped.map(({ group_type, groups: gs }) => (
              <div key={group_type} className="mb-1">
                <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground font-medium">
                  {TYPE_LABELS[group_type]}
                </div>
                {gs.map((g) => (
                  <button
                    key={g.id}
                    type="button"
                    onClick={() => addGroup(g)}
                    className="w-full text-left px-3 py-1.5 text-sm hover:bg-accent flex items-center gap-2"
                  >
                    <Check className="h-3.5 w-3.5 opacity-0" />
                    <span>{g.name}</span>
                    {g.description && (
                      <span className="text-xs text-muted-foreground truncate">— {g.description}</span>
                    )}
                  </button>
                ))}
              </div>
            ))}
          </div>

          <div className="border-t bg-secondary/40">
            <button
              type="button"
              onClick={() => setShowAddNew(true)}
              className="w-full text-left px-3 py-2 text-xs hover:bg-accent flex items-center gap-2"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>Create new group</span>
            </button>
          </div>

          {showAddNew && (
            <AddNewGroupInlineForm
              defaultName={search}
              onCancel={() => setShowAddNew(false)}
              onCreated={(group) => {
                addGroup(group);
                setShowAddNew(false);
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Inline "+ Create new group" form
// --------------------------------------------------------------------------
function AddNewGroupInlineForm({
  defaultName,
  onCancel,
  onCreated,
}: {
  defaultName: string;
  onCancel: () => void;
  onCreated: (group: Group) => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<GroupCreate>({
    name: defaultName || "",
    description: "",
    group_type: "CUSTOM",
    enabled: true,
  });
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async (body: GroupCreate) =>
      api.post<Group>("/groups", {
        ...body,
        description: (body.description ?? "").trim() || null,
      }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["groups"] });
      onCreated(created);
    },
    onError: (e: Error) => setError(e.message),
  });

  function submit() {
    setError(null);
    if (!form.name.trim()) {
      setError("Name is required.");
      return;
    }
    create.mutate(form);
  }

  return (
    <div className="absolute inset-0 bg-popover p-3 space-y-2 overflow-auto">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold">Create new group</h4>
        <button
          type="button"
          onClick={onCancel}
          className="text-muted-foreground hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new-group-name" className="text-xs">Name *</Label>
        <Input
          id="new-group-name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="North Plant Compressors"
          className="h-8 text-sm"
          autoFocus
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new-group-type" className="text-xs">Type</Label>
        <select
          id="new-group-type"
          value={form.group_type ?? "CUSTOM"}
          onChange={(e) => setForm({ ...form, group_type: e.target.value as GroupType })}
          className="w-full h-8 rounded-md border bg-background px-2 text-sm"
        >
          {GROUP_TYPES.map((t) => (
            <option key={t} value={t}>{TYPE_LABELS[t]}</option>
          ))}
        </select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new-group-desc" className="text-xs">Description (optional)</Label>
        <Input
          id="new-group-desc"
          value={form.description ?? ""}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder="Optional notes"
          className="h-8 text-sm"
        />
      </div>
      {error && (
        <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
          {error}
        </div>
      )}
      <div className="flex gap-2 pt-1">
        <Button
          type="button"
          size="sm"
          onClick={submit}
          disabled={create.isPending}
          className="flex-1"
        >
          {create.isPending ? "Creating…" : "Create and add"}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
