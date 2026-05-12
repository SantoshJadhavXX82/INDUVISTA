/**
 * Engineering Units Master — CRUD UI for the global unit list.
 *
 * Lists all units grouped by quantity_kind. Filter by enabled, search by
 * code/label. Create new (non-system) units. Edit any unit (including
 * seed entries — but is_system can't be flipped). Delete only non-system
 * unused entries; disable is the right move for everything else.
 *
 * Phase 8.1
 */
import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Pencil, Trash2, Shield, AlertCircle, Search } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Drawer } from "@/components/ui/drawer";
import { HelpTip } from "@/components/ui/help-tip";
import { api, ApiError } from "@/lib/api";
import { help } from "@/lib/help-text";
import { cn } from "@/lib/utils";
import type {
  EngineeringUnit, EngineeringUnitCreate, EngineeringUnitUpdate,
} from "@/types/api";

export default function EngineeringUnits() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [showDisabled, setShowDisabled] = useState(true);
  const [filterKind, setFilterKind] = useState<string>("");
  const [createDrawerOpen, setCreateDrawerOpen] = useState(false);
  const [editingUnit, setEditingUnit] = useState<EngineeringUnit | null>(null);

  const unitsQuery = useQuery<EngineeringUnit[]>({
    queryKey: ["engineering-units", "all"],
    queryFn: () => api.get("/engineering-units"),
    staleTime: 30_000,
  });

  // Available kinds for the filter dropdown
  const kinds = useMemo(() => {
    const set = new Set<string>();
    (unitsQuery.data ?? []).forEach((u) => {
      if (u.quantity_kind) set.add(u.quantity_kind);
    });
    return Array.from(set).sort();
  }, [unitsQuery.data]);

  // Filtered + grouped data for display
  const grouped = useMemo(() => {
    const all = unitsQuery.data ?? [];
    const q = search.trim().toLowerCase();
    const filtered = all.filter((u) => {
      if (!showDisabled && !u.enabled) return false;
      if (filterKind && u.quantity_kind !== filterKind) return false;
      if (q) {
        const haystack = `${u.code} ${u.label} ${u.quantity_kind ?? ""}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
    const groups = new Map<string, EngineeringUnit[]>();
    filtered.forEach((u) => {
      const key = u.quantity_kind ?? "_other";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(u);
    });
    return Array.from(groups.entries())
      .map(([kind, units]) => ({ kind, units: units.sort((a, b) => a.code.localeCompare(b.code)) }))
      .sort((a, b) => a.kind.localeCompare(b.kind));
  }, [unitsQuery.data, search, filterKind, showDisabled]);

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.patch(`/engineering-units/${id}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["engineering-units"] }),
  });

  const totalCount = unitsQuery.data?.length ?? 0;
  const enabledCount = (unitsQuery.data ?? []).filter((u) => u.enabled).length;
  const customCount = (unitsQuery.data ?? []).filter((u) => !u.is_system).length;

  return (
    <div className="p-6 space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Engineering Units</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Global master list of engineering units. Tags pick from this list,
            or override with a custom value for one-off cases.
          </p>
        </div>
        <Button onClick={() => setCreateDrawerOpen(true)} size="sm">
          <Plus className="h-4 w-4 mr-1.5" /> New unit
        </Button>
      </header>

      <div className="grid grid-cols-3 gap-3">
        <Card>
          <CardContent className="pt-4">
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Total</div>
            <div className="text-2xl font-bold tabular-nums">{totalCount}</div>
            <div className="text-xs text-muted-foreground">{enabledCount} enabled</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="text-xs text-muted-foreground uppercase tracking-wide">System seeded</div>
            <div className="text-2xl font-bold tabular-nums">{totalCount - customCount}</div>
            <div className="text-xs text-muted-foreground">Protected from deletion</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="text-xs text-muted-foreground uppercase tracking-wide">User custom</div>
            <div className="text-2xl font-bold tabular-nums">{customCount}</div>
            <div className="text-xs text-muted-foreground">Editable + deletable</div>
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
            placeholder="Search by code, label, or kind…"
            className="pl-7 h-9"
          />
        </div>
        <select
          value={filterKind}
          onChange={(e) => setFilterKind(e.target.value)}
          className="h-9 rounded-md border bg-background px-3 text-sm"
        >
          <option value="">All kinds</option>
          {kinds.map((k) => (
            <option key={k} value={k}>{k}</option>
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
      {unitsQuery.isLoading && (
        <Card><CardContent className="p-6 text-sm text-muted-foreground">Loading…</CardContent></Card>
      )}
      {grouped.length === 0 && !unitsQuery.isLoading && (
        <Card><CardContent className="p-6 text-sm text-muted-foreground text-center">
          No units match your filters.
        </CardContent></Card>
      )}
      {grouped.map(({ kind, units }) => (
        <Card key={kind}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold capitalize text-muted-foreground">
              {kind === "_other" ? "Other" : kind.replace(/_/g, " ")}
              <span className="ml-2 text-xs font-normal">({units.length})</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[20%]">Code</TableHead>
                  <TableHead className="w-[35%]">Label</TableHead>
                  <TableHead className="w-[25%]">Description</TableHead>
                  <TableHead className="text-right w-[8%]">In use</TableHead>
                  <TableHead className="text-center w-[6%]">Type</TableHead>
                  <TableHead className="text-right w-[6%]">Status</TableHead>
                  <TableHead className="text-right w-[100px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {units.map((u) => (
                  <TableRow key={u.id} className={cn(!u.enabled && "opacity-60")}>
                    <TableCell className="font-mono text-sm">{u.code || <span className="text-muted-foreground italic">(blank)</span>}</TableCell>
                    <TableCell>{u.label}</TableCell>
                    <TableCell className="text-xs text-muted-foreground truncate max-w-xs">
                      {u.description ?? ""}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <span className={cn(u.in_use_count > 0 && "font-semibold")}>{u.in_use_count}</span>
                    </TableCell>
                    <TableCell className="text-center">
                      {u.is_system ? (
                        <span title="System-seeded (protected from deletion)">
                          <Shield className="h-3.5 w-3.5 inline-block text-blue-600" />
                        </span>
                      ) : (
                        <span className="text-[10px] uppercase text-muted-foreground tracking-wide">user</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <button
                        type="button"
                        onClick={() => toggleEnabled.mutate({ id: u.id, enabled: !u.enabled })}
                        className={cn(
                          "text-[10px] uppercase tracking-wide px-2 py-0.5 rounded",
                          u.enabled
                            ? "bg-green-100 text-green-800 hover:bg-green-200"
                            : "bg-secondary text-muted-foreground hover:bg-secondary/80",
                        )}
                        title="Click to toggle enabled"
                      >
                        {u.enabled ? "on" : "off"}
                      </button>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditingUnit(u)}
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

      <Drawer open={createDrawerOpen} onClose={() => setCreateDrawerOpen(false)} title="New engineering unit">
        <UnitForm
          mode="create"
          onSaved={() => setCreateDrawerOpen(false)}
        />
      </Drawer>

      <Drawer open={!!editingUnit} onClose={() => setEditingUnit(null)} title={`Edit unit: ${editingUnit?.code ?? ""}`}>
        {editingUnit && (
          <UnitForm
            mode="edit"
            unit={editingUnit}
            onSaved={() => setEditingUnit(null)}
            onDeleted={() => setEditingUnit(null)}
          />
        )}
      </Drawer>
    </div>
  );
}

// --------------------------------------------------------------------------
// Shared create + edit form
// --------------------------------------------------------------------------
function UnitForm({
  mode,
  unit,
  onSaved,
  onDeleted,
}: {
  mode: "create" | "edit";
  unit?: EngineeringUnit;
  onSaved: () => void;
  onDeleted?: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<EngineeringUnitCreate>({
    code: unit?.code ?? "",
    label: unit?.label ?? "",
    quantity_kind: unit?.quantity_kind ?? "",
    enabled: unit?.enabled ?? true,
    description: unit?.description ?? "",
  });
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async (body: EngineeringUnitCreate | EngineeringUnitUpdate) => {
      const cleaned = {
        ...body,
        quantity_kind: (body.quantity_kind ?? "").toString().trim() || null,
        description: (body.description ?? "").toString().trim() || null,
      };
      return mode === "create"
        ? api.post<EngineeringUnit>("/engineering-units", cleaned)
        : api.patch<EngineeringUnit>(`/engineering-units/${unit!.id}`, cleaned);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["engineering-units"] });
      onSaved();
    },
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/engineering-units/${unit!.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["engineering-units"] });
      onDeleted?.();
    },
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!form.code.trim() || !form.label.trim()) {
      setError("Code and label are required.");
      return;
    }
    save.mutate(form);
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="unit-code">
          Code <span className="text-red-600">*</span>
          <HelpTip entry={help.engineering_unit.code} />
        </Label>
        <Input
          id="unit-code"
          value={form.code}
          onChange={(e) => setForm({ ...form, code: e.target.value })}
          placeholder="kg/h"
          autoFocus
        />
        <p className="text-xs text-muted-foreground">
          Short symbol shown next to values. Unicode is fine (°C, m³, μS/cm).
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="unit-label">
          Label <span className="text-red-600">*</span>
          <HelpTip entry={help.engineering_unit.label} />
        </Label>
        <Input
          id="unit-label"
          value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
          placeholder="Kilograms per hour"
        />
        <p className="text-xs text-muted-foreground">Human-readable name shown in dropdowns.</p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="unit-kind">
          Quantity kind <HelpTip entry={help.engineering_unit.quantity_kind} />
        </Label>
        <Input
          id="unit-kind"
          value={form.quantity_kind ?? ""}
          onChange={(e) => setForm({ ...form, quantity_kind: e.target.value })}
          placeholder="flow_mass"
        />
        <p className="text-xs text-muted-foreground">
          Optional. Lowercase snake_case for grouping in the dropdown. Reuse an
          existing kind (e.g. <code className="font-mono bg-secondary px-1 rounded">flow_mass</code>,{" "}
          <code className="font-mono bg-secondary px-1 rounded">pressure</code>) when possible.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="unit-desc">
          Description <HelpTip entry={help.engineering_unit.description} />
        </Label>
        <Input
          id="unit-desc"
          value={form.description ?? ""}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder="Optional notes"
        />
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          className="h-4 w-4"
        />
        <span>Enabled</span>
        <span className="text-xs text-muted-foreground">
          (when off, hidden from tag dropdowns but existing references stay valid)
        </span>
      </label>

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2 flex gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex gap-2 pt-2">
        <Button type="submit" disabled={save.isPending} className="flex-1">
          {save.isPending ? "Saving…" : mode === "create" ? "Create unit" : "Save changes"}
        </Button>
      </div>

      {mode === "edit" && unit && (
        <div className="pt-4 mt-4 border-t border-red-100 space-y-2">
          <h3 className="text-sm font-semibold text-red-700">Delete unit</h3>
          {unit.is_system ? (
            <p className="text-xs text-muted-foreground">
              <Shield className="h-3 w-3 inline-block mr-1 text-blue-600" />
              System-seeded units cannot be deleted. Disable instead.
            </p>
          ) : unit.in_use_count > 0 ? (
            <p className="text-xs text-muted-foreground">
              {unit.in_use_count} tag{unit.in_use_count !== 1 && "s"} reference this unit.
              Reassign them first, then delete.
            </p>
          ) : (
            <Button
              type="button"
              variant="outline"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
              className="text-red-700 border-red-200 hover:bg-red-50"
            >
              <Trash2 className="h-3.5 w-3.5 mr-1.5" />
              {remove.isPending ? "Deleting…" : "Delete unit"}
            </Button>
          )}
        </div>
      )}
    </form>
  );
}
