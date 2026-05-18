/**
 * Phase 15.3 - Calc Blocks admin page.
 * Phase 16.0b - Adds schema-driven "New calculation" modal.
 * Phase 16.0c - Adds Value column, Edit/Delete/Toggle actions.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Calculator, RefreshCw, AlertTriangle, ChevronDown, ChevronRight,
  CheckCircle2, XCircle, Clock, Zap, Power, Plus, Pencil, Trash2, Loader2,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

import { useCalcDefinitions, useBlockTypes } from "@/lib/useCalcDefinitions";
import type { CalcDefinition, BlockType } from "@/types/calcDefinitions";

import { CreateCalcModal } from "@/components/calc/CreateCalcModal";


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


// ---------------------------------------------------------------------------
// Hook: poll current values for all output tags every 2 seconds
// ---------------------------------------------------------------------------

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
  const types = useBlockTypes();
  const values = useCurrentValues();
  const qc = useQueryClient();

  const [filterCategory, setFilterCategory] = useState<string>("all");
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [filterEnabledOnly, setFilterEnabledOnly] = useState(false);
  const [filterSearch, setFilterSearch] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingCalc, setEditingCalc] = useState<CalcDefinition | null>(null);

  // Mutation: toggle enabled. PUTs the full def with `enabled` flipped.
  const toggleMutation = useMutation({
    mutationFn: async (def: CalcDefinition) => {
      const body = {
        tag_id: def.tag_id,
        block_type: def.block_type,
        block_config: def.block_config,
        execution_rate_ms: def.execution_rate_ms,
        enabled: !def.enabled,
      };
      const res = await fetch(`/api/calc/definitions/${def.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`Toggle failed (HTTP ${res.status}): ${detail}`);
      }
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["calc-definitions"] }),
    onError: (err: Error) => alert(err.message),
  });

  // Mutation: delete.
  const deleteMutation = useMutation({
    mutationFn: async (def: CalcDefinition) => {
      const res = await fetch(`/api/calc/definitions/${def.id}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) {
        const detail = await res.text();
        throw new Error(`Delete failed (HTTP ${res.status}): ${detail}`);
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["calc-definitions"] }),
    onError: (err: Error) => alert(err.message),
  });

  function handleDelete(def: CalcDefinition) {
    const tagName = def.tag_name ?? `tag #${def.tag_id}`;
    const ok = window.confirm(
      `Delete calc #${def.id} (${def.block_type} → ${tagName})?\n\n` +
      `The output tag itself will NOT be deleted; only the calculation` +
      ` definition is removed. The tag's last value will remain in history.`
    );
    if (!ok) return;
    deleteMutation.mutate(def);
  }

  function handleEdit(def: CalcDefinition) {
    setEditingCalc(def);
    setModalOpen(true);
  }

  function handleCreate() {
    setEditingCalc(null);
    setModalOpen(true);
  }

  function handleModalClose() {
    setModalOpen(false);
    setEditingCalc(null);
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
          (d.tag_name ?? "") + " " + d.block_type + " " + (d.id?.toString() ?? "")
        ).toLowerCase();
        if (!hay.includes(search)) return false;
      }
      return true;
    });
  }, [defs.data, filterCategory, filterStatus, filterEnabledOnly, filterSearch, typeByCode]);

  const valueLookup = values.data?.values ?? {};

  if (defs.isLoading) {
    return (
      <div className="p-6 flex items-center justify-center text-muted-foreground">
        <RefreshCw className="h-4 w-4 animate-spin mr-2" />
        Loading calc definitions...
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
              <div className="font-medium">Failed to load calc definitions</div>
              <pre className="text-xs text-muted-foreground mt-1">{String(defs.error)}</pre>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between py-3 px-4 border-b border-border">
          <div className="flex items-center gap-2">
            <Calculator className="h-4 w-4" />
            <CardTitle className="text-sm">Calc Blocks</CardTitle>
            <span className="text-[11px] text-muted-foreground">
              {filtered.length} of {defs.data?.length ?? 0}
            </span>
          </div>
          <button
            type="button"
            onClick={handleCreate}
            className="text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground
                       hover:bg-primary/90 inline-flex items-center gap-1.5"
          >
            <Plus className="h-3 w-3" />
            New calculation
          </button>
        </CardHeader>

        <CardContent className="p-3">
          {/* Filter bar */}
          <div className="flex flex-wrap items-center gap-2 mb-3 text-xs">
            <Input
              placeholder="Search tag, type, id..."
              className="h-7 w-48 text-xs"
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

          {filtered.length === 0 ? (
            <div className="text-xs text-muted-foreground italic py-4 text-center">
              No calc definitions match the current filters.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-muted-foreground text-[10px] uppercase tracking-wider">
                    <th className="w-4 p-0"></th>
                    <th className="text-right px-2 py-2 font-medium">ID</th>
                    <th className="text-left px-3 py-2 font-medium">Output tag</th>
                    <th className="text-left px-3 py-2 font-medium">Block type</th>
                    <th className="text-right px-3 py-2 font-medium">Rate</th>
                    <th className="text-right px-3 py-2 font-medium">Current value</th>
                    <th className="text-center px-3 py-2 font-medium">Status</th>
                    <th className="text-right px-3 py-2 font-medium">Last run</th>
                    <th className="text-right px-3 py-2 font-medium">Runs</th>
                    <th className="text-right px-3 py-2 font-medium">Errors</th>
                    <th className="text-center px-3 py-2 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((d) => (
                    <CalcDefRow
                      key={d.id}
                      def={d}
                      type={typeByCode.get(d.block_type)}
                      expanded={expandedId === d.id}
                      currentValue={valueLookup[String(d.tag_id)]}
                      onToggle={() =>
                        setExpandedId(expandedId === d.id ? null : d.id)
                      }
                      onToggleEnabled={() => toggleMutation.mutate(d)}
                      onEdit={() => handleEdit(d)}
                      onDelete={() => handleDelete(d)}
                      toggling={toggleMutation.isPending && toggleMutation.variables?.id === d.id}
                      deleting={deleteMutation.isPending && deleteMutation.variables?.id === d.id}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="px-1 text-[11px] text-muted-foreground space-y-1">
        <p>
          <strong>Current value</strong> column polls every 2 seconds from{" "}
          <code className="text-[10px]">/api/calc/current-values</code>
          {values.data?._source && (
            <> (source: <code>{values.data._source}</code>)</>
          )}
          {values.data?._note && (
            <span className="text-amber-600"> · {values.data._note}</span>
          )}.
        </p>
        <p>
          <strong>Actions.</strong> Edit reopens the form for changes;
          delete removes the calc definition only (the output tag stays).
          Toggle uses PUT to flip the enabled flag.
        </p>
      </div>

      <CreateCalcModal
        open={modalOpen}
        onClose={handleModalClose}
        existingCalc={editingCalc}
      />
    </div>
  );
}


// ---------------------------------------------------------------------------
// Row + detail panel
// ---------------------------------------------------------------------------

function CalcDefRow({
  def, type, expanded, currentValue, onToggle,
  onToggleEnabled, onEdit, onDelete, toggling, deleting,
}: {
  def: CalcDefinition;
  type: BlockType | undefined;
  expanded: boolean;
  currentValue: CurrentValueRecord | undefined;
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
  const lastRun = def.last_executed_at ? relativeTime(def.last_executed_at) : "-";

  // Format the current-value cell. Quality 192 = GOOD_NON_SPECIFIC, 0-127 = BAD.
  const cv = currentValue;
  const valueDisplay = (() => {
    if (!cv) return <span className="text-muted-foreground">-</span>;
    if (cv.value === null || cv.value === undefined) {
      return (
        <span className="text-amber-600" title={cv.quality != null ? `quality ${cv.quality}` : "no value"}>
          BAD
        </span>
      );
    }
    const isBoolish = def.block_type.match(/^(GT|LT|EQ|NE|GE|LE|AND|OR|NOT|TON|TOF|TP|R_TRIG|F_TRIG|SR|RS)$/);
    const formatted = isBoolish
      ? (cv.value > 0.5 ? "TRUE" : "FALSE")
      : (Math.abs(cv.value) >= 0.01 && Math.abs(cv.value) < 1e9
          ? cv.value.toFixed(4)
          : cv.value.toExponential(3));
    const tooltip = cv.ts
      ? `last written ${relativeTime(cv.ts)} (quality ${cv.quality ?? "?"})`
      : `quality ${cv.quality ?? "?"}`;
    return (
      <span className="font-mono tabular-nums" title={tooltip}>
        {formatted}
      </span>
    );
  })();

  return (
    <>
      <tr
        className="border-t border-border hover:bg-secondary/30"
      >
        <td className="px-1 text-muted-foreground cursor-pointer" onClick={onToggle}>
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </td>
        <td className="px-2 py-1.5 text-right tabular-nums text-muted-foreground cursor-pointer" onClick={onToggle}>
          {def.id}
        </td>
        <td className="px-3 py-1.5 font-medium cursor-pointer" onClick={onToggle}>
          {def.tag_name ?? <span className="text-muted-foreground italic">(unresolved tag #{def.tag_id})</span>}
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
        <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground cursor-pointer" onClick={onToggle}>
          {def.total_executions}
        </td>
        <td
          className={`px-3 py-1.5 text-right tabular-nums cursor-pointer ${
            def.total_errors > 0 ? "text-red-600 font-medium" : "text-muted-foreground"
          }`}
          onClick={onToggle}
        >
          {def.total_errors}
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
          <td colSpan={11} className="p-4">
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
                  Stats
                </div>
                <table className="text-xs">
                  <tbody>
                    <tr><td className="pr-3 text-muted-foreground">Total executions</td><td className="tabular-nums">{def.total_executions}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Total overruns</td><td className={`tabular-nums ${def.total_overruns > 0 ? "text-amber-600" : ""}`}>{def.total_overruns}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Total errors</td><td className={`tabular-nums ${def.total_errors > 0 ? "text-red-600" : ""}`}>{def.total_errors}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Output tag ID</td><td className="tabular-nums">{def.tag_id}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Current value</td><td className="tabular-nums font-mono">
                      {currentValue?.value != null
                        ? currentValue.value.toString()
                        : <span className="text-muted-foreground">-</span>}
                      {currentValue?.ts && (
                        <span className="ml-2 text-[10px] text-muted-foreground">
                          {relativeTime(currentValue.ts)}
                        </span>
                      )}
                    </td></tr>
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
