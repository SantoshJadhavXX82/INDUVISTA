/**
 * UnitSelect — engineering-unit picker with three modes:
 *   1. Pick from master (grouped dropdown by quantity_kind)
 *   2. Add new to master inline (creates an entry, then selects it)
 *   3. Custom override (free text, stored in tags.engineering_unit)
 *
 * Maintains exactly-one semantics: setting an FK clears the override and vice
 * versa. The DB CHECK constraint backs this up if anything slips through.
 *
 * Phase 8.1
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Plus, Pencil, X, Search, Check } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { EngineeringUnit, EngineeringUnitCreate } from "@/types/api";

export type UnitSelectValue = {
  // Exactly one of these is non-null. Both null = unitless.
  engineering_unit_id: number | null;
  engineering_unit: string | null;
};

const QUANTITY_KIND_LABELS: Record<string, string> = {
  temperature: "Temperature",
  pressure: "Pressure",
  flow_volume: "Flow — Volumetric",
  flow_mass: "Flow — Mass",
  length: "Length / Level",
  volume: "Volume",
  mass: "Mass",
  energy: "Energy",
  power: "Power",
  voltage: "Voltage",
  current: "Current",
  resistance: "Resistance",
  frequency: "Frequency",
  rotation: "Rotation",
  velocity: "Velocity",
  time: "Time",
  density: "Density",
  concentration: "Concentration",
  viscosity: "Viscosity",
  conductivity: "Conductivity",
  turbidity: "Turbidity",
  ph: "pH",
  heating_value: "Heating value",
  humidity: "Humidity",
  illuminance: "Illuminance",
  ratio: "Ratio",
  dimensionless: "Dimensionless",
};

function prettifyKind(kind: string | null | undefined): string {
  if (!kind) return "Other";
  return (
    QUANTITY_KIND_LABELS[kind] ??
    kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

export function UnitSelect({
  value,
  onChange,
  disabled,
}: {
  value: UnitSelectValue;
  onChange: (v: UnitSelectValue) => void;
  disabled?: boolean;
}) {
  const [mode, setMode] = useState<"master" | "custom">(
    value.engineering_unit && !value.engineering_unit_id ? "custom" : "master",
  );
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [showAddNew, setShowAddNew] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
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

  // Fetch all units (cached — they change rarely)
  const unitsQuery = useQuery<EngineeringUnit[]>({
    queryKey: ["engineering-units"],
    queryFn: () => api.get("/engineering-units?enabled=true"),
    staleTime: 60_000,
  });

  // Group + filter
  const groupedUnits = useMemo(() => {
    const all = unitsQuery.data ?? [];
    const q = search.trim().toLowerCase();
    const filtered = q
      ? all.filter(
          (u) =>
            u.code.toLowerCase().includes(q) ||
            u.label.toLowerCase().includes(q) ||
            (u.quantity_kind ?? "").toLowerCase().includes(q),
        )
      : all;
    const groups = new Map<string, EngineeringUnit[]>();
    filtered.forEach((u) => {
      const key = u.quantity_kind ?? "_other";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(u);
    });
    return Array.from(groups.entries())
      .map(([kind, units]) => ({ kind, units }))
      .sort((a, b) => prettifyKind(a.kind).localeCompare(prettifyKind(b.kind)));
  }, [unitsQuery.data, search]);

  const selectedUnit = useMemo(
    () =>
      value.engineering_unit_id
        ? unitsQuery.data?.find((u) => u.id === value.engineering_unit_id) ?? null
        : null,
    [value.engineering_unit_id, unitsQuery.data],
  );

  // Display label shown in the closed state
  const displayLabel =
    mode === "custom" && value.engineering_unit
      ? value.engineering_unit
      : selectedUnit
      ? `${selectedUnit.code} — ${selectedUnit.label}`
      : "";

  function selectUnit(unit: EngineeringUnit) {
    onChange({ engineering_unit_id: unit.id, engineering_unit: null });
    setMode("master");
    setOpen(false);
    setShowAddNew(false);
    setSearch("");
  }

  function switchToCustom() {
    onChange({ engineering_unit_id: null, engineering_unit: "" });
    setMode("custom");
    setOpen(false);
    setShowAddNew(false);
  }

  function clearAll() {
    onChange({ engineering_unit_id: null, engineering_unit: null });
    setMode("master");
  }

  // CUSTOM mode — render a text input with a small chip
  if (mode === "custom") {
    return (
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Input
            value={value.engineering_unit ?? ""}
            onChange={(e) =>
              onChange({ engineering_unit_id: null, engineering_unit: e.target.value })
            }
            placeholder="e.g. Nm³/h per stream"
            disabled={disabled}
            className="pr-20"
          />
          <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] uppercase tracking-wide bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded">
            custom
          </span>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setMode("master")}
          disabled={disabled}
          title="Switch back to master picker"
        >
          <Pencil className="h-3.5 w-3.5 mr-1" /> Master
        </Button>
      </div>
    );
  }

  // MASTER mode — combo box
  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className={cn(
          "w-full flex items-center justify-between rounded-md border bg-background",
          "px-3 py-2 text-sm h-9 hover:border-foreground/30 disabled:opacity-50",
          !displayLabel && "text-muted-foreground",
        )}
      >
        <span className="truncate text-left">
          {displayLabel || "Select an engineering unit…"}
        </span>
        <div className="flex items-center gap-1 shrink-0">
          {displayLabel && (
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => {
                e.stopPropagation();
                clearAll();
              }}
              className="text-muted-foreground hover:text-foreground p-0.5"
              title="Clear unit"
            >
              <X className="h-3.5 w-3.5" />
            </span>
          )}
          <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition", open && "rotate-180")} />
        </div>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full max-h-96 overflow-hidden rounded-md border bg-popover shadow-md flex flex-col">
          {/* Search bar */}
          <div className="p-2 border-b">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search code or label…"
                className="pl-7 h-8 text-sm"
                autoFocus
              />
            </div>
          </div>

          {/* Grouped list */}
          <div className="flex-1 overflow-auto py-1">
            {unitsQuery.isLoading && (
              <div className="px-3 py-2 text-xs text-muted-foreground">Loading…</div>
            )}
            {!unitsQuery.isLoading && groupedUnits.length === 0 && (
              <div className="px-3 py-2 text-xs text-muted-foreground">
                No units match "{search}"
              </div>
            )}
            {groupedUnits.map(({ kind, units }) => (
              <div key={kind} className="mb-1">
                <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground font-medium">
                  {prettifyKind(kind)}
                </div>
                {units.map((u) => (
                  <button
                    key={u.id}
                    type="button"
                    onClick={() => selectUnit(u)}
                    className="w-full text-left px-3 py-1.5 text-sm hover:bg-accent flex items-center gap-2"
                  >
                    {value.engineering_unit_id === u.id && (
                      <Check className="h-3.5 w-3.5 text-foreground" />
                    )}
                    <span className={cn(
                      "font-mono text-xs",
                      value.engineering_unit_id !== u.id && "ml-5",
                    )}>
                      {u.code}
                    </span>
                    <span className="text-muted-foreground text-xs truncate">— {u.label}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>

          {/* Footer escape hatches */}
          <div className="border-t bg-secondary/40">
            <button
              type="button"
              onClick={() => setShowAddNew(true)}
              className="w-full text-left px-3 py-2 text-xs hover:bg-accent flex items-center gap-2"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>Add new to master</span>
            </button>
            <button
              type="button"
              onClick={switchToCustom}
              className="w-full text-left px-3 py-2 text-xs hover:bg-accent flex items-center gap-2 border-t"
            >
              <Pencil className="h-3.5 w-3.5" />
              <span>Custom value (override for this tag only)</span>
            </button>
          </div>

          {showAddNew && (
            <AddNewUnitInlineForm
              defaultCode={search}
              onCancel={() => setShowAddNew(false)}
              onCreated={(unit) => selectUnit(unit)}
            />
          )}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Inline "+ Add new to master" form, popped over the dropdown footer
// --------------------------------------------------------------------------
function AddNewUnitInlineForm({
  defaultCode,
  onCancel,
  onCreated,
}: {
  defaultCode: string;
  onCancel: () => void;
  onCreated: (unit: EngineeringUnit) => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<EngineeringUnitCreate>({
    code: defaultCode || "",
    label: "",
    quantity_kind: "",
    enabled: true,
  });
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async (body: EngineeringUnitCreate) =>
      api.post<EngineeringUnit>("/engineering-units", body),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["engineering-units"] });
      onCreated(created);
    },
    onError: (e: Error) => setError(e.message),
  });

  function submit() {
    setError(null);
    if (!form.code.trim() || !form.label.trim()) {
      setError("Code and label are required.");
      return;
    }
    create.mutate({
      ...form,
      quantity_kind: form.quantity_kind?.trim() || null,
    });
  }

  return (
    <div className="absolute inset-x-0 bottom-0 top-0 bg-popover p-3 space-y-2 overflow-auto">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold">Add new unit to master</h4>
        <button
          type="button"
          onClick={onCancel}
          className="text-muted-foreground hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new-unit-code" className="text-xs">Code *</Label>
        <Input
          id="new-unit-code"
          value={form.code}
          onChange={(e) => setForm({ ...form, code: e.target.value })}
          placeholder="kg/h"
          className="h-8 text-sm"
          autoFocus
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new-unit-label" className="text-xs">Label *</Label>
        <Input
          id="new-unit-label"
          value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
          placeholder="Kilograms per hour"
          className="h-8 text-sm"
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new-unit-kind" className="text-xs">Quantity kind (optional)</Label>
        <Input
          id="new-unit-kind"
          value={form.quantity_kind ?? ""}
          onChange={(e) => setForm({ ...form, quantity_kind: e.target.value })}
          placeholder="flow_mass"
          className="h-8 text-sm"
        />
        <p className="text-[10px] text-muted-foreground">
          Lowercase snake_case for grouping. Reuse an existing kind if you can.
        </p>
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
          {create.isPending ? "Creating…" : "Create and select"}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
