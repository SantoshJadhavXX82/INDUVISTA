/**
 * Enumerations Master — CRUD UI for value→label state-machine sets.
 *
 * UI is labeled "Enumerations" to avoid colliding with DeltaV's "Named Set"
 * terminology. The underlying storage and API still use `named_sets` /
 * `/api/named-sets` to keep code, types, and DB-level references stable.
 *
 * Each enumeration is a small lookup table. The list view shows all sets
 * with their value count and usage. The edit drawer shows the metadata
 * fields plus an inline-editable table of values (add row, remove row,
 * reorder via display_order).
 *
 * Phase 8.3 / renamed in 8.5.x
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Pencil, Trash2, Shield, AlertCircle, Search, GripVertical, X,
} from "lucide-react";

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
import type {
  NamedSet, NamedSetValue, NamedSetCreate,
} from "@/types/api";

export default function NamedSets() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [showDisabled, setShowDisabled] = useState(true);
  const [createDrawerOpen, setCreateDrawerOpen] = useState(false);
  const [editingSet, setEditingSet] = useState<NamedSet | null>(null);

  const setsQuery = useQuery<NamedSet[]>({
    queryKey: ["named-sets", "all"],
    queryFn: () => api.get("/named-sets?include_values=true"),
    staleTime: 30_000,
  });

  const filtered = useMemo(() => {
    const all = setsQuery.data ?? [];
    const q = search.trim().toLowerCase();
    return all
      .filter((s) => {
        if (!showDisabled && !s.enabled) return false;
        if (q) {
          const haystack = `${s.name} ${s.description ?? ""}`.toLowerCase();
          if (!haystack.includes(q)) return false;
        }
        return true;
      })
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [setsQuery.data, search, showDisabled]);

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.patch(`/named-sets/${id}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["named-sets"] }),
  });

  const totalCount = setsQuery.data?.length ?? 0;
  const enabledCount = (setsQuery.data ?? []).filter((s) => s.enabled).length;
  const systemCount = (setsQuery.data ?? []).filter((s) => s.is_system).length;
  const totalUsage = (setsQuery.data ?? []).reduce((sum, s) => sum + s.in_use_count, 0);

  return (
    <div className="p-6 space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Enumerations</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Lookup tables that translate raw integer values into human-readable
            text. Assign one to a tag to show "Running" instead of "1" in dashboards
            and reports — the raw value is still stored as the CV.
          </p>
        </div>
        <Button onClick={() => setCreateDrawerOpen(true)} size="sm">
          <Plus className="h-4 w-4 mr-1.5" /> New enumeration
        </Button>
      </header>

      <div className="grid grid-cols-4 gap-3">
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
            <div className="text-2xl font-bold tabular-nums">{systemCount}</div>
            <div className="text-xs text-muted-foreground">Protected from deletion</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="text-xs text-muted-foreground uppercase tracking-wide">User custom</div>
            <div className="text-2xl font-bold tabular-nums">{totalCount - systemCount}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Tags using</div>
            <div className="text-2xl font-bold tabular-nums">{totalUsage}</div>
          </CardContent>
        </Card>
      </div>

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

      {setsQuery.isLoading && (
        <Card><CardContent className="p-6 text-sm text-muted-foreground">Loading…</CardContent></Card>
      )}
      {filtered.length === 0 && !setsQuery.isLoading && (
        <Card><CardContent className="p-6 text-sm text-muted-foreground text-center">
          No enumerations match your filters.
        </CardContent></Card>
      )}
      {filtered.length > 0 && (
        <Card>
          <CardContent className="pt-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[25%]">Name</TableHead>
                  <TableHead className="w-[30%]">Description</TableHead>
                  <TableHead className="w-[20%]">Preview</TableHead>
                  <TableHead className="text-right w-[6%]">Values</TableHead>
                  <TableHead className="text-right w-[6%]">In use</TableHead>
                  <TableHead className="text-center w-[5%]">Type</TableHead>
                  <TableHead className="text-right w-[6%]">Status</TableHead>
                  <TableHead className="text-right w-[60px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((s) => {
                  const preview = s.values
                    .slice(0, 2)
                    .map((v) => `${v.raw_value}=${v.display_text}`)
                    .join(", ");
                  const more = s.values.length > 2 ? `, +${s.values.length - 2}` : "";
                  return (
                    <TableRow key={s.id} className={cn(!s.enabled && "opacity-60")}>
                      <TableCell className="font-mono text-sm font-medium">{s.name}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {s.description ?? <span className="italic">—</span>}
                      </TableCell>
                      <TableCell className="text-xs font-mono text-muted-foreground">
                        {preview}{more}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{s.value_count}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        <span className={cn(s.in_use_count > 0 && "font-semibold")}>{s.in_use_count}</span>
                      </TableCell>
                      <TableCell className="text-center">
                        {s.is_system ? (
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
                          onClick={() => toggleEnabled.mutate({ id: s.id, enabled: !s.enabled })}
                          className={cn(
                            "text-[10px] uppercase tracking-wide px-2 py-0.5 rounded",
                            s.enabled
                              ? "bg-green-100 text-green-800 hover:bg-green-200"
                              : "bg-secondary text-muted-foreground hover:bg-secondary/80",
                          )}
                        >
                          {s.enabled ? "on" : "off"}
                        </button>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditingSet(s)}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Drawer
        open={createDrawerOpen}
        onClose={() => setCreateDrawerOpen(false)}
        title="New enumeration"
      >
        <NamedSetForm
          mode="create"
          onSaved={() => setCreateDrawerOpen(false)}
        />
      </Drawer>

      <Drawer
        open={!!editingSet}
        onClose={() => setEditingSet(null)}
        title={`Edit: ${editingSet?.name ?? ""}`}
        size="lg"
      >
        {editingSet && (
          <NamedSetForm
            mode="edit"
            namedSet={editingSet}
            onSaved={() => setEditingSet(null)}
            onDeleted={() => setEditingSet(null)}
          />
        )}
      </Drawer>
    </div>
  );
}

// --------------------------------------------------------------------------
// Shared create + edit form
// --------------------------------------------------------------------------
type EditableValue = {
  // tracking key for React renders; -1 for newly-added rows
  key: number;
  raw_value: string;       // strings so we can validate numerics on submit
  display_text: string;
  display_order: string;
  color: string;
};

function NamedSetForm({
  mode,
  namedSet,
  onSaved,
  onDeleted,
}: {
  mode: "create" | "edit";
  namedSet?: NamedSet;
  onSaved: () => void;
  onDeleted?: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<NamedSetCreate>({
    name: namedSet?.name ?? "",
    description: namedSet?.description ?? "",
    enabled: namedSet?.enabled ?? true,
  });
  const [values, setValues] = useState<EditableValue[]>(() =>
    (namedSet?.values ?? []).map((v) => ({
      key: v.id,
      raw_value: String(v.raw_value),
      display_text: v.display_text,
      display_order: String(v.display_order),
      color: v.color ?? "",
    })),
  );
  const [error, setError] = useState<string | null>(null);

  // For new sets, start with two blank rows so users see the shape
  useEffect(() => {
    if (mode === "create" && values.length === 0) {
      setValues([
        { key: -1, raw_value: "0", display_text: "", display_order: "0", color: "" },
        { key: -2, raw_value: "1", display_text: "", display_order: "1", color: "" },
      ]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveMetadata = useMutation({
    mutationFn: async () => {
      const body = {
        ...form,
        description: (form.description ?? "").toString().trim() || null,
      };
      return mode === "create"
        ? api.post<NamedSet>("/named-sets", body)
        : api.patch<NamedSet>(`/named-sets/${namedSet!.id}`, body);
    },
  });

  const saveValues = useMutation({
    mutationFn: async (setId: number) => {
      const cleaned = values
        .filter((v) => v.display_text.trim() !== "")
        .map((v) => ({
          raw_value: parseInt(v.raw_value, 10),
          display_text: v.display_text.trim(),
          display_order: parseInt(v.display_order, 10) || 0,
          color: v.color.trim() || null,
        }));
      return api.put(`/named-sets/${setId}/values`, { values: cleaned });
    },
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/named-sets/${namedSet!.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["named-sets"] });
      onDeleted?.();
    },
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!form.name.trim()) {
      setError("Name is required.");
      return;
    }
    // Validate values: unique raw_value, non-empty display_text on at least one
    const cleanedValues = values.filter((v) => v.display_text.trim() !== "");
    if (cleanedValues.length === 0) {
      setError("At least one value with a display text is required.");
      return;
    }
    const rawSeen = new Set<number>();
    for (const v of cleanedValues) {
      const raw = parseInt(v.raw_value, 10);
      if (Number.isNaN(raw)) {
        setError(`Row with text "${v.display_text}": raw_value must be a number.`);
        return;
      }
      if (rawSeen.has(raw)) {
        setError(`Duplicate raw_value ${raw} — each must be unique.`);
        return;
      }
      rawSeen.add(raw);
    }

    try {
      const saved = await saveMetadata.mutateAsync();
      await saveValues.mutateAsync(saved.id);
      qc.invalidateQueries({ queryKey: ["named-sets"] });
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : (e as Error).message);
    }
  }

  function addRow() {
    const nextRaw = Math.max(0, ...values.map((v) => parseInt(v.raw_value, 10) || 0)) + 1;
    setValues([
      ...values,
      {
        key: -(values.length + 1) * 1000 - Math.random(),
        raw_value: String(nextRaw),
        display_text: "",
        display_order: String(values.length),
        color: "",
      },
    ]);
  }

  function removeRow(key: number) {
    setValues(values.filter((v) => v.key !== key));
  }

  function updateRow(key: number, field: keyof EditableValue, val: string) {
    setValues(values.map((v) => (v.key === key ? { ...v, [field]: val } : v)));
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="ns-name">
          Name <span className="text-red-600">*</span>
          <HelpTip entry={help.named_set.name} />
        </Label>
        <Input
          id="ns-name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="MOTOR_STATE"
          autoFocus
          className="font-mono"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="ns-desc">
          Description <HelpTip entry={help.named_set.description} />
        </Label>
        <Input
          id="ns-desc"
          value={form.description ?? ""}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder="What this set represents"
        />
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.enabled ?? true}
          onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          className="h-4 w-4"
        />
        <span>Enabled</span>
        <span className="text-xs text-muted-foreground">
          (when off, hidden from tag dropdowns)
        </span>
      </label>

      <div className="space-y-2 border-t pt-3">
        <div className="flex items-center justify-between">
          <Label>Values</Label>
          <Button type="button" variant="outline" size="sm" onClick={addRow}>
            <Plus className="h-3.5 w-3.5 mr-1" /> Add value
          </Button>
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[60px]">
                Order <HelpTip entry={help.named_set.display_order} />
              </TableHead>
              <TableHead className="w-[80px]">
                Raw <HelpTip entry={help.named_set.raw_value} />
              </TableHead>
              <TableHead>
                Display text <HelpTip entry={help.named_set.display_text} />
              </TableHead>
              <TableHead className="w-[100px]">
                Color <HelpTip entry={help.named_set.color} />
              </TableHead>
              <TableHead className="w-[40px]"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {values.map((v) => (
              <TableRow key={v.key}>
                <TableCell className="p-1">
                  <Input
                    type="number"
                    value={v.display_order}
                    onChange={(e) => updateRow(v.key, "display_order", e.target.value)}
                    className="h-7 text-xs tabular-nums"
                  />
                </TableCell>
                <TableCell className="p-1">
                  <Input
                    type="number"
                    value={v.raw_value}
                    onChange={(e) => updateRow(v.key, "raw_value", e.target.value)}
                    className="h-7 text-xs tabular-nums font-mono"
                  />
                </TableCell>
                <TableCell className="p-1">
                  <Input
                    value={v.display_text}
                    onChange={(e) => updateRow(v.key, "display_text", e.target.value)}
                    placeholder="Running"
                    className="h-7 text-xs"
                  />
                </TableCell>
                <TableCell className="p-1">
                  <Input
                    value={v.color}
                    onChange={(e) => updateRow(v.key, "color", e.target.value)}
                    placeholder="green"
                    className="h-7 text-xs"
                  />
                </TableCell>
                <TableCell className="p-1 text-right">
                  <button
                    type="button"
                    onClick={() => removeRow(v.key)}
                    className="text-muted-foreground hover:text-red-700"
                    title="Remove this value"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </TableCell>
              </TableRow>
            ))}
            {values.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-xs text-muted-foreground py-4">
                  No values yet — click "Add value" to define at least one.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2 flex gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex gap-2 pt-2">
        <Button
          type="submit"
          disabled={saveMetadata.isPending || saveValues.isPending}
          className="flex-1"
        >
          {saveMetadata.isPending || saveValues.isPending
            ? "Saving…"
            : mode === "create" ? "Create enumeration" : "Save changes"}
        </Button>
      </div>

      {mode === "edit" && namedSet && (
        <div className="pt-4 mt-4 border-t border-red-100 space-y-2">
          <h3 className="text-sm font-semibold text-red-700">Delete</h3>
          {namedSet.is_system ? (
            <p className="text-xs text-muted-foreground">
              <Shield className="h-3 w-3 inline-block mr-1 text-blue-600" />
              System-seeded sets cannot be deleted. Disable instead.
            </p>
          ) : namedSet.in_use_count > 0 ? (
            <p className="text-xs text-muted-foreground">
              {namedSet.in_use_count} tag(s) reference this set. Reassign or
              clear them first.
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
              {remove.isPending ? "Deleting…" : "Delete enumeration"}
            </Button>
          )}
        </div>
      )}
    </form>
  );
}
