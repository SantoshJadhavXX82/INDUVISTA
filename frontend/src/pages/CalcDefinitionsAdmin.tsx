/**
 * Phase 17.0b - Computed Tags admin page.
 *
 * Adds visual indicators for external output targets:
 *   - Row: small "→ device/tag" badge next to the tag name
 *   - Detail panel: explicit Output target rows
 *
 * Listing is grouped by Computed Device with collapse/expand. Each
 * device section has a per-device "+ tag" button which opens
 * CreateCalcModal pre-selected to that device.
 *
 * Header includes a "Manage Computed Devices" button that opens
 * ComputedDevicesModal for device CRUD.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Calculator, RefreshCw, AlertTriangle, ChevronDown, ChevronRight,
  CheckCircle2, XCircle, Clock, Zap, Power, Plus, Pencil, Trash2, Loader2,
  Settings, FolderOpen, Folder, ArrowRight,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Gate } from "@/lib/rbac";
import { SectionCard } from "@/components/ui/section-card";
import { formatFloat } from "@/lib/format";

import {
  useCalcDefinitions, useBlockTypes, useComputedDevices,
  CALC_DEFINITIONS_QUERY_KEY,
} from "@/lib/useCalcDefinitions";
import type {
  CalcDefinition, BlockType, ComputedDevice,
} from "@/types/calcDefinitions";

import { CreateCalcModal } from "@/components/calc/CreateCalcModal";
import { ComputedDevicesModal } from "@/components/calc/ComputedDevicesModal";
import { ConfirmDialog } from "@/components/ConfirmDialog";


const STATUS_STYLES: Record<string, string> = {
  ok:       "bg-emerald-50 text-emerald-800 border-emerald-300",
  error:    "bg-red-50 text-red-800 border-red-300",
  overrun:  "bg-amber-50 text-amber-800 border-amber-300",
  killed:   "bg-red-100 text-red-900 border-red-400",
  pending:  "bg-slate-100 text-slate-600 border-slate-300",
};

const CATEGORY_STYLES: Record<string, string> = {
  aggregation:    "bg-sky-50 text-sky-800 border-sky-300",
  selection:      "bg-purple-50 text-purple-800 border-purple-300",
  conditional:    "bg-amber-50 text-amber-800 border-amber-300",
  comparison:     "bg-cyan-50 text-cyan-800 border-cyan-300",
  logical:        "bg-emerald-50 text-emerald-800 border-emerald-300",
  timer:          "bg-violet-50 text-violet-800 border-violet-300",
  edge_detector:  "bg-pink-50 text-pink-800 border-pink-300",
  latch:          "bg-orange-50 text-orange-800 border-orange-300",
  counter:        "bg-teal-50 text-teal-800 border-teal-300",
  arithmetic:     "bg-indigo-50 text-indigo-800 border-indigo-300",
  unary_math:     "bg-lime-50 text-lime-800 border-lime-300",
  transcendental: "bg-rose-50 text-rose-800 border-rose-300",
};


interface CurrentValueRecord {
  value: number | null;
  quality: number | null;
  ts: string | null;
}
interface CurrentValuesResponse {
  values: Record<string, CurrentValueRecord>;
  _source?: string;
  _note?: string;
}
function useCurrentValues() {
  return useQuery<CurrentValuesResponse>({
    queryKey: ["calc-current-values"],
    queryFn: async () => {
      const res = await fetch("/api/calc/current-values");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    refetchInterval: 2000,
    staleTime: 1000,
  });
}


export default function CalcDefinitionsAdmin() {
  const defs = useCalcDefinitions();
  const devices = useComputedDevices();
  const types = useBlockTypes();
  const values = useCurrentValues();
  const qc = useQueryClient();

  const [filterCategory, setFilterCategory] = useState<string>("all");
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [filterEnabledOnly, setFilterEnabledOnly] = useState(false);
  const [filterSearch, setFilterSearch] = useState("");

  const [collapsed, setCollapsed] = useState<Record<number, boolean>>({});
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [devicesModalOpen, setDevicesModalOpen] = useState(false);
  const [editingCalc, setEditingCalc] = useState<CalcDefinition | null>(null);
  const [initialDeviceId, setInitialDeviceId] = useState<number | null>(null);

  const [pendingToggle, setPendingToggle] = useState<CalcDefinition | null>(null);
  const [pendingDelete, setPendingDelete] = useState<CalcDefinition | null>(null);

  const toggleMutation = useMutation({
    mutationFn: async (d: CalcDefinition) => {
      const res = await fetch(`/api/computed-tags/${d.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !d.enabled }),
      });
      if (!res.ok) {
        throw new Error(`Toggle failed (HTTP ${res.status}): ${await res.text()}`);
      }
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: CALC_DEFINITIONS_QUERY_KEY }),
    onError: (err: Error) => alert(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async (d: CalcDefinition) => {
      const res = await fetch(`/api/computed-tags/${d.id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        throw new Error(`Delete failed (HTTP ${res.status}): ${await res.text()}`);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: CALC_DEFINITIONS_QUERY_KEY });
      qc.invalidateQueries({ queryKey: ["computed-devices"] });
    },
    onError: (err: Error) => alert(err.message),
  });

  function handleEdit(def: CalcDefinition) {
    setEditingCalc(def);
    setInitialDeviceId(null);
    setModalOpen(true);
  }

  function handleCreateGlobal() {
    setEditingCalc(null);
    setInitialDeviceId(null);
    setModalOpen(true);
  }

  function handleCreateInDevice(deviceId: number) {
    setEditingCalc(null);
    setInitialDeviceId(deviceId);
    setModalOpen(true);
  }

  function handleModalClose() {
    setModalOpen(false);
    setEditingCalc(null);
    setInitialDeviceId(null);
  }

  const typeByCode = useMemo(() => {
    const map = new Map<string, BlockType>();
    (types.data ?? []).forEach((t) => map.set(t.code, t));
    return map;
  }, [types.data]);

  const categories = useMemo(() => {
    const cats = new Set<string>();
    (types.data ?? []).forEach((t) => cats.add(t.category));
    return Array.from(cats).sort();
  }, [types.data]);

  const filtered = useMemo(() => {
    const all = defs.data ?? [];
    const search = filterSearch.trim().toLowerCase();
    return all.filter((d) => {
      if (filterEnabledOnly && !d.enabled) return false;
      if (filterStatus !== "all" && (d.last_status ?? "pending") !== filterStatus) return false;
      if (filterCategory !== "all") {
        const t = typeByCode.get(d.block_type);
        if (!t || t.category !== filterCategory) return false;
      }
      if (search) {
        const hay = (
          (d.name ?? "") + " " + d.block_type + " " +
          (d.device_name ?? "") + " " + (d.id?.toString() ?? "") + " " +
          (d.output_tag_name ?? "") + " " + (d.output_device_name ?? "")
        ).toLowerCase();
        if (!hay.includes(search)) return false;
      }
      return true;
    });
  }, [defs.data, filterCategory, filterStatus, filterEnabledOnly, filterSearch, typeByCode]);

  const groupedByDevice = useMemo(() => {
    const map = new Map<number, CalcDefinition[]>();
    for (const d of filtered) {
      if (!map.has(d.device_id)) map.set(d.device_id, []);
      map.get(d.device_id)!.push(d);
    }
    for (const arr of map.values()) {
      arr.sort((a, b) => a.name.localeCompare(b.name));
    }
    return map;
  }, [filtered]);

  const allDevices = useMemo(() => {
    return [...(devices.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  }, [devices.data]);

  const valueLookup = values.data?.values ?? {};
  const totalCount = defs.data?.length ?? 0;

  if (defs.isLoading || devices.isLoading) {
    return (
      <div className="p-6 flex items-center justify-center text-muted-foreground">
        <RefreshCw className="h-4 w-4 animate-spin mr-2" />
        Loading computed tags…
      </div>
    );
  }

  if (defs.isError) {
    return (
      <div className="p-6">
        <Card className="border-destructive">
          <CardContent className="p-4 flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 text-destructive flex-shrink-0 mt-0.5" />
            <div className="text-sm">
              <div className="font-medium">Failed to load computed tags</div>
              <pre className="text-xs text-muted-foreground mt-1">{String(defs.error)}</pre>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        title="Calc tags"
        subtitle={`${filtered.length} of ${totalCount} · ${allDevices.length} device${allDevices.length !== 1 ? "s" : ""}`}
        actions={
          <>
            <button
              type="button"
              onClick={() => setDevicesModalOpen(true)}
              className="text-xs px-2.5 py-1 rounded inline-flex items-center gap-1.5 hover:bg-secondary"
              style={{ border: "0.5px solid var(--separator)" }}
            >
              <Settings className="h-3 w-3" />
              Manage devices
            </button>
            <Gate cap="configure">
            <button
              type="button"
              onClick={handleCreateGlobal}
              disabled={allDevices.filter((d) => d.enabled).length === 0}
              title={allDevices.filter((d) => d.enabled).length === 0
                ? "Create a Computed Device first"
                : "Create a new computed tag"}
              className="text-xs px-2.5 py-1 rounded text-white inline-flex items-center gap-1.5 disabled:opacity-40"
              style={{ backgroundColor: "var(--ios-blue)" }}
            >
              <Plus className="h-3 w-3" />
              New computed tag
            </button>
            </Gate>
          </>
        }
      />
      <SectionCard flush>
        <div className="p-3">
          <div className="flex flex-wrap items-center gap-2 mb-3 text-xs">
            <Input
              placeholder="Search name, type, device, target…"
              className="h-7 w-56 text-xs"
              value={filterSearch}
              onChange={(e) => setFilterSearch(e.target.value)}
            />
            <select
              className="h-7 text-xs bg-card border border-border rounded px-2"
              value={filterCategory}
              onChange={(e) => setFilterCategory(e.target.value)}
            >
              <option value="all">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <select
              className="h-7 text-xs bg-card border border-border rounded px-2"
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
            >
              <option value="all">All statuses</option>
              <option value="ok">ok</option>
              <option value="error">error</option>
              <option value="overrun">overrun</option>
              <option value="killed">killed</option>
              <option value="pending">pending</option>
            </select>
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={filterEnabledOnly}
                onChange={(e) => setFilterEnabledOnly(e.target.checked)}
              />
              Enabled only
            </label>
          </div>

          {allDevices.length === 0 ? (
            <div className="text-center py-10 border border-dashed border-border rounded">
              <Calculator className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
              <div className="text-sm font-medium mb-1">No Computed Devices yet</div>
              <p className="text-xs text-muted-foreground mb-3 max-w-md mx-auto">
                A computed tag must live on a Computed Device. Create one first.
              </p>
              <button
                type="button"
                onClick={() => setDevicesModalOpen(true)}
                className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground
                           hover:bg-primary/90 inline-flex items-center gap-1.5"
              >
                <Plus className="h-3 w-3" />
                Create your first Computed Device
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              {allDevices.map((device) => (
                <DeviceGroup
                  key={device.id}
                  device={device}
                  defs={groupedByDevice.get(device.id) ?? []}
                  collapsed={collapsed[device.id] ?? false}
                  onToggleCollapse={() =>
                    setCollapsed({ ...collapsed, [device.id]: !(collapsed[device.id] ?? false) })
                  }
                  typeByCode={typeByCode}
                  valueLookup={valueLookup}
                  expandedId={expandedId}
                  onToggleExpanded={setExpandedId}
                  onCreateHere={() => handleCreateInDevice(device.id)}
                  onEdit={handleEdit}
                  onToggleEnabled={(d) => setPendingToggle(d)}
                  onDelete={(d) => setPendingDelete(d)}
                  toggling={toggleMutation.isPending ? toggleMutation.variables?.id : null}
                  deleting={deleteMutation.isPending ? deleteMutation.variables?.id : null}
                />
              ))}

              {filtered.length === 0 && allDevices.length > 0 && (
                <div className="text-xs text-muted-foreground italic text-center py-3">
                  No computed tags match the current filters.
                </div>
              )}
            </div>
          )}
        </div>
      </SectionCard>

      <div className="px-1 text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
        <p>
          <strong>Current value</strong> column polls every 2 seconds from{" "}
          <code className="text-[10px]">/api/calc/current-values</code>.
          {" "}For calcs with external output, the Value cell shows the
          internal anchor's last value (typically NULL — values flow to the
          external tag instead).
        </p>
      </div>

      <CreateCalcModal
        open={modalOpen}
        onClose={handleModalClose}
        existingCalc={editingCalc}
        initialDeviceId={initialDeviceId}
      />
      <ComputedDevicesModal
        open={devicesModalOpen}
        onClose={() => setDevicesModalOpen(false)}
      />

      <ConfirmDialog
        open={!!pendingToggle}
        title={
          pendingToggle?.enabled
            ? `Disable computed tag #${pendingToggle?.id}?`
            : `Enable computed tag #${pendingToggle?.id}?`
        }
        description={
          pendingToggle?.enabled ? (
            <>
              The calculation <strong>{pendingToggle?.block_type}</strong> writing to{" "}
              <strong>
                {pendingToggle?.output_tag_id != null
                  ? `${pendingToggle.output_device_name} / ${pendingToggle.output_tag_name}`
                  : pendingToggle?.name ?? `tag #${pendingToggle?.id}`}
              </strong>{" "}
              will stop being evaluated. History is preserved.
            </>
          ) : (
            <>
              The calculation <strong>{pendingToggle?.block_type}</strong> will resume every{" "}
              {pendingToggle ? Math.round(pendingToggle.execution_rate_ms / 1000) : "?"}s.
            </>
          )
        }
        confirmLabel={pendingToggle?.enabled ? "Disable" : "Enable"}
        severity={pendingToggle?.enabled ? "warning" : "normal"}
        busy={toggleMutation.isPending}
        onConfirm={() => {
          if (pendingToggle) {
            toggleMutation.mutate(pendingToggle, {
              onSettled: () => setPendingToggle(null),
            });
          }
        }}
        onCancel={() => setPendingToggle(null)}
      />

      <ConfirmDialog
        open={!!pendingDelete}
        title={`Delete computed tag "${pendingDelete?.name}"?`}
        description={
          <>
            Computed tag <strong>{pendingDelete?.name}</strong> (<strong>{pendingDelete?.block_type}</strong>){" "}
            will be removed permanently — including its definition, execution stats, and historical values.
            {pendingDelete?.output_tag_id != null && (
              <>
                <br />
                The external output target{" "}
                <strong>{pendingDelete.output_device_name} / {pendingDelete.output_tag_name}</strong>{" "}
                will continue to exist but will no longer receive values from this calc.
              </>
            )}
            <br />
            <span className="text-destructive">This cannot be undone.</span>
          </>
        }
        confirmLabel="Delete"
        severity="destructive"
        busy={deleteMutation.isPending}
        onConfirm={() => {
          if (pendingDelete) {
            deleteMutation.mutate(pendingDelete, {
              onSettled: () => setPendingDelete(null),
            });
          }
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}


// ---------------------------------------------------------------------------
// Device group section
// ---------------------------------------------------------------------------

interface DeviceGroupProps {
  device: ComputedDevice;
  defs: CalcDefinition[];
  collapsed: boolean;
  onToggleCollapse: () => void;
  typeByCode: Map<string, BlockType>;
  valueLookup: Record<string, CurrentValueRecord>;
  expandedId: number | null;
  onToggleExpanded: (id: number | null) => void;
  onCreateHere: () => void;
  onEdit: (d: CalcDefinition) => void;
  onToggleEnabled: (d: CalcDefinition) => void;
  onDelete: (d: CalcDefinition) => void;
  toggling: number | null | undefined;
  deleting: number | null | undefined;
}

function DeviceGroup({
  device, defs, collapsed, onToggleCollapse,
  typeByCode, valueLookup, expandedId, onToggleExpanded,
  onCreateHere, onEdit, onToggleEnabled, onDelete,
  toggling, deleting,
}: DeviceGroupProps) {
  return (
    <div className="border border-border rounded">
      <div className="flex items-center justify-between px-3 py-2 bg-secondary/20 border-b border-border">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="flex items-center gap-2 text-xs font-medium hover:text-foreground"
        >
          {collapsed
            ? <Folder className="h-3.5 w-3.5 text-muted-foreground" />
            : <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
          }
          {collapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          <span>{device.name}</span>
          <span className="text-[10px] text-muted-foreground">
            ({defs.length} of {device.computed_tag_count})
          </span>
          {!device.enabled && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-300">
              device disabled
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={onCreateHere}
          disabled={!device.enabled}
          title={device.enabled ? "Add a computed tag to this device" : "Enable the device first"}
          className="text-[10px] px-2 py-0.5 rounded border border-border
                     hover:bg-card disabled:opacity-30 inline-flex items-center gap-1"
        >
          <Plus className="h-2.5 w-2.5" />
          tag
        </button>
      </div>

      {!collapsed && (
        defs.length === 0 ? (
          <div className="text-[11px] text-muted-foreground italic px-3 py-3">
            {device.computed_tag_count === 0
              ? "No computed tags on this device yet."
              : "No tags on this device match the current filters."}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground text-[10px] uppercase tracking-wider border-b border-border">
                  <th className="w-4 p-0"></th>
                  <th className="text-right px-2 py-2 font-medium">ID</th>
                  <th className="text-left px-3 py-2 font-medium">Tag name</th>
                  <th className="text-left px-3 py-2 font-medium">Block type</th>
                  <th className="text-right px-3 py-2 font-medium">Rate</th>
                  <th className="text-right px-3 py-2 font-medium">Value</th>
                  <th className="text-center px-3 py-2 font-medium">Status</th>
                  <th className="text-right px-3 py-2 font-medium">Last run</th>
                  <th className="text-center px-3 py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {defs.map((d) => (
                  <CalcDefRow
                    key={d.id}
                    def={d}
                    type={typeByCode.get(d.block_type)}
                    expanded={expandedId === d.id}
                    currentValue={valueLookup[String(d.id)]}
                    externalValue={d.output_tag_id != null
                      ? valueLookup[String(d.output_tag_id)] : undefined}
                    onToggle={() =>
                      onToggleExpanded(expandedId === d.id ? null : d.id)
                    }
                    onToggleEnabled={() => onToggleEnabled(d)}
                    onEdit={() => onEdit(d)}
                    onDelete={() => onDelete(d)}
                    toggling={toggling === d.id}
                    deleting={deleting === d.id}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Per-tag row + detail panel
// ---------------------------------------------------------------------------

function CalcDefRow({
  def, type, expanded, currentValue, externalValue, onToggle,
  onToggleEnabled, onEdit, onDelete, toggling, deleting,
}: {
  def: CalcDefinition;
  type: BlockType | undefined;
  expanded: boolean;
  currentValue: CurrentValueRecord | undefined;
  externalValue: CurrentValueRecord | undefined;
  onToggle: () => void;
  onToggleEnabled: () => void;
  onEdit: () => void;
  onDelete: () => void;
  toggling: boolean;
  deleting: boolean;
}) {
  const status = def.last_status ?? "pending";
  const statusCls = STATUS_STYLES[status] ?? STATUS_STYLES.pending;
  const catCls = type
    ? (CATEGORY_STYLES[type.category] ?? "bg-slate-50 text-slate-700 border-slate-300")
    : "bg-slate-50";

  const rateLabel = formatRate(def.execution_rate_ms);
  const lastRun = def.last_executed_at ? relativeTime(def.last_executed_at) : "—";

  const isExternal = def.output_tag_id != null;
  // For display: prefer the external value when in external mode, since
  // the internal anchor will be NULL.
  const displayedValue = isExternal ? externalValue : currentValue;

  const valueDisplay = (() => {
    if (!displayedValue) return <span className="text-muted-foreground">—</span>;
    if (displayedValue.value === null || displayedValue.value === undefined) {
      return (
        <span className="text-amber-600"
              title={displayedValue.quality != null ? `quality ${displayedValue.quality}` : "no value"}>
          BAD
        </span>
      );
    }
    const isBoolish = def.block_type.match(/^(GT|LT|EQ|NE|GE|LE|AND|OR|NOT|TON|TOF|TP|R_TRIG|F_TRIG|SR|RS)$/);
    const formatted = isBoolish
      ? (displayedValue.value > 0.5 ? "TRUE" : "FALSE")
      : formatFloat(displayedValue.value);
    const tooltip = displayedValue.ts
      ? `last written ${relativeTime(displayedValue.ts)} (quality ${displayedValue.quality ?? "?"})${isExternal ? " — read from external target" : ""}`
      : `quality ${displayedValue.quality ?? "?"}`;
    return (
      <span className="font-mono tabular-nums" title={tooltip}>
        {formatted}
      </span>
    );
  })();

  return (
    <>
      <tr className="border-t border-border hover:bg-secondary/30">
        <td className="px-1 text-muted-foreground cursor-pointer" onClick={onToggle}>
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </td>
        <td className="px-2 py-1.5 text-right tabular-nums text-muted-foreground cursor-pointer" onClick={onToggle}>
          {def.id}
        </td>
        <td className="px-3 py-1.5 cursor-pointer" onClick={onToggle}>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium">{def.name}</span>
            <span className="text-[10px] text-muted-foreground font-mono">{def.data_type}</span>
            {isExternal && (
              <span
                className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded
                           bg-violet-50 text-violet-800 border border-violet-300 font-mono"
                title={`Writes to ${def.output_device_name} / ${def.output_tag_name}`}
              >
                <ArrowRight className="h-2.5 w-2.5" />
                {def.output_device_name}/{def.output_tag_name}
              </span>
            )}
          </div>
        </td>
        <td className="px-3 py-1.5 cursor-pointer" onClick={onToggle}>
          <span className={`inline-block text-[10px] px-1.5 py-0.5 rounded border ${catCls}`}
                title={type?.description ?? ""}>
            {type?.label ?? def.block_type}
          </span>
        </td>
        <td className="px-3 py-1.5 text-right tabular-nums font-mono text-[11px] cursor-pointer" onClick={onToggle}>
          {rateLabel}
        </td>
        <td className="px-3 py-1.5 text-right cursor-pointer" onClick={onToggle}>
          {valueDisplay}
        </td>
        <td className="px-3 py-1.5 text-center cursor-pointer" onClick={onToggle}>
          <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${statusCls}`}>
            {statusIcon(status)}
            {status}
          </span>
        </td>
        <td className="px-3 py-1.5 text-right text-muted-foreground tabular-nums cursor-pointer" onClick={onToggle}>
          {lastRun}
        </td>
        <td className="px-3 py-1.5">
          <div className="flex items-center justify-center gap-1">
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onToggleEnabled(); }}
              disabled={toggling}
              title={def.enabled ? "Disable" : "Enable"}
              className="h-6 w-6 inline-flex items-center justify-center rounded
                         hover:bg-secondary disabled:opacity-30"
            >
              {toggling ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Power className={`h-3 w-3 ${
                  def.enabled ? "text-emerald-600" : "text-muted-foreground opacity-40"
                }`} />
              )}
            </button>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onEdit(); }}
              title="Edit"
              className="h-6 w-6 inline-flex items-center justify-center rounded
                         hover:bg-secondary text-muted-foreground hover:text-foreground"
            >
              <Pencil className="h-3 w-3" />
            </button>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onDelete(); }}
              disabled={deleting}
              title="Delete"
              className="h-6 w-6 inline-flex items-center justify-center rounded
                         hover:bg-destructive/10 text-muted-foreground
                         hover:text-destructive disabled:opacity-30"
            >
              {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
            </button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-border bg-secondary/15">
          <td colSpan={9} className="p-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                  Block config
                </div>
                <pre className="text-[11px] font-mono bg-card border border-border rounded p-2 overflow-x-auto">
                  {JSON.stringify(def.block_config, null, 2)}
                </pre>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                  Stats & routing
                </div>
                {/* Phase 17 — diagnostic banner. When last_error_message
                    is populated, the worker has classified the problem
                    into a "Title — Cause. Action." sentence. Render it
                    prominently so operators don't have to squint at a
                    10px table cell. Severity color depends on status:
                      error/killed → red (action required)
                      ok + msg     → amber (warning: BAD-quality output) */}
                {def.last_error_message && (
                  <DiagnosticBanner
                    severity={
                      def.last_status === 'error' || def.last_status === 'killed'
                        ? 'error' : 'warning'
                    }
                    status={def.last_status}
                    message={def.last_error_message}
                  />
                )}
                <table className="text-xs">
                  <tbody>
                    <tr><td className="pr-3 text-muted-foreground">Last status</td><td>{def.last_status ?? "pending"}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Last duration</td><td className="tabular-nums">{def.last_duration_ms != null ? `${def.last_duration_ms.toFixed(3)} ms` : "—"}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Tag id</td><td className="tabular-nums">{def.id}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Device</td><td className="text-[10px]">{def.device_name} (#{def.device_id})</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Data type</td><td className="text-[10px] font-mono">{def.data_type}</td></tr>
                    {/* Phase 17.0b - output routing */}
                    <tr>
                      <td className="pr-3 text-muted-foreground">Output target</td>
                      <td className="text-[10px]">
                        {isExternal ? (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded
                                           bg-violet-50 text-violet-800 border border-violet-300 font-mono">
                            external <ArrowRight className="h-2.5 w-2.5" />{" "}
                            {def.output_device_name} / {def.output_tag_name} (#{def.output_tag_id})
                          </span>
                        ) : (
                          <span className="text-muted-foreground italic">
                            internal (writes to own tag #{def.id})
                          </span>
                        )}
                      </td>
                    </tr>
                    {def.description && (
                      <tr><td className="pr-3 text-muted-foreground">Description</td><td className="text-[10px]">{def.description}</td></tr>
                    )}
                    <tr>
                      <td className="pr-3 text-muted-foreground">Internal value</td>
                      <td className="tabular-nums font-mono text-[10px]">
                        {currentValue?.value != null
                          ? currentValue.value.toString()
                          : <span className="text-muted-foreground">—</span>}
                        {currentValue?.ts && (
                          <span className="ml-2 text-[10px] text-muted-foreground">
                            {relativeTime(currentValue.ts)}
                          </span>
                        )}
                      </td>
                    </tr>
                    {isExternal && (
                      <tr>
                        <td className="pr-3 text-muted-foreground">External value</td>
                        <td className="tabular-nums font-mono text-[10px]">
                          {externalValue?.value != null
                            ? externalValue.value.toString()
                            : <span className="text-muted-foreground">—</span>}
                          {externalValue?.ts && (
                            <span className="ml-2 text-[10px] text-muted-foreground">
                              {relativeTime(externalValue.ts)}
                            </span>
                          )}
                        </td>
                      </tr>
                    )}
                    <tr><td className="pr-3 text-muted-foreground">Created</td><td className="text-[10px]">{new Date(def.created_at).toLocaleString()}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Updated</td><td className="text-[10px]">{new Date(def.updated_at).toLocaleString()}</td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Phase 17 — operator-friendly diagnostic banner.
 *
 * The worker's classify_error() formats every error as:
 *   "Title — Cause. Action."
 *
 * This banner parses that grammar and renders:
 *   - Title in bold (the headline)
 *   - Cause + Action as a body paragraph
 *
 * Severity drives the color:
 *   - error    : red (last_status='error' or 'killed') — operator action required
 *   - warning  : amber (last_status='ok' but quality propagation set the message)
 */
function DiagnosticBanner({
  severity,
  status,
  message,
}: {
  severity: 'error' | 'warning';
  status: string | null;
  message: string;
}) {
  // Try to split "Title — rest of message" so we can render the title prominently.
  const emDashIdx = message.indexOf(' — ');
  const title = emDashIdx > 0 ? message.slice(0, emDashIdx) : message;
  const body = emDashIdx > 0 ? message.slice(emDashIdx + 3) : null;

  const styles = severity === 'error'
    ? {
        container: 'bg-red-50 border-red-300 text-red-900',
        badge: 'bg-red-200 text-red-900',
        badgeLabel: status === 'killed' ? 'TIMEOUT' : 'ERROR',
      }
    : {
        container: 'bg-amber-50 border-amber-300 text-amber-900',
        badge: 'bg-amber-200 text-amber-900',
        badgeLabel: 'WARNING',
      };

  return (
    <div className={`mt-2 mb-3 rounded border px-3 py-2 ${styles.container}`}>
      <div className="flex items-start gap-2">
        <span className={`text-[10px] font-bold uppercase tracking-wider
                         rounded px-1.5 py-0.5 mt-0.5 ${styles.badge}`}>
          {styles.badgeLabel}
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold leading-snug">{title}</div>
          {body && (
            <div className="text-[11px] leading-snug mt-1 opacity-90">
              {body}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


function formatRate(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${ms / 1000} s`;
  if (ms < 3_600_000) return `${ms / 60_000} min`;
  return `${ms / 3_600_000} h`;
}

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 0) return "in future";
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

function statusIcon(status: string) {
  switch (status) {
    case "ok":      return <CheckCircle2 className="h-2.5 w-2.5" />;
    case "error":   return <XCircle className="h-2.5 w-2.5" />;
    case "overrun": return <Zap className="h-2.5 w-2.5" />;
    case "killed":  return <XCircle className="h-2.5 w-2.5" />;
    default:        return <Clock className="h-2.5 w-2.5" />;
  }
}
