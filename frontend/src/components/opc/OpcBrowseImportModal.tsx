/**
 * OPC Browse & Import — Phase OPC-web.2.2.
 *
 * Wide drawer that lets operators browse an OPC source's live address
 * space, tick the variable nodes they want to ingest, and bulk-import
 * them as INDUVISTA tags + mappings in one shot.
 *
 *   Tree pane (left)            Selected pane (right)
 *   ────────────────            ─────────────────────
 *   ▼ CONDENSATE1               ─ KPW_CUR_DAILY_MASS
 *     ▼ FLC1                       <prefix><leafname>     [type] [unit]
 *       ▼ MTR1                  ─ KPW_CUR_DAILY_GUVOL
 *         ☑ KPW_CUR_DAILY_MASS     ...
 *         ☑ KPW_CUR_DAILY_GUVOL   Total: 3 tags
 *         ☐ ...                  [ Import 3 tags ]
 *
 * Design decisions (some non-obvious):
 *
 *  • Browse is LAZY. We never preload the whole tree — only the
 *    children of the node the operator just clicked. Backend endpoint
 *    is `/api/opc-sources/{id}/browse?node_id=<id>`. Default node_id
 *    'ObjectsFolder' returns the top-level Kepware projects.
 *
 *  • System folders (browse_name starting with '_', plus the UA-standard
 *    Server folder at i=2253) are flagged is_system=True by the backend.
 *    By default the tree filters them out; a "Show system folders" toggle
 *    in the toolbar exposes them when needed (rare, for debugging).
 *
 *  • Selection state is keyed by node_id. Once a node is ticked it stays
 *    in the selection set even if the operator collapses the folder and
 *    re-expands it — selection survives navigation.
 *
 *  • Already-mapped variables (is_mapped=true) are shown disabled with
 *    a small badge. The operator sees them so they know what they've
 *    already done, but can't pick them again — preventing the easy-to-
 *    make mistake of duplicating mappings.
 *
 *  • Auto-generated tag names use the LEAF browse_name lowercased,
 *    optionally prepended with a user-typed prefix. Per-row override
 *    is possible. Full-path prefix vs. leaf-only is a deliberate design
 *    choice: leaves are concise; if the operator wants disambiguation,
 *    they type a prefix like `condensate1.mtr1.`.
 *
 *  • Bulk import shows a per-row results panel after the request returns,
 *    matching the backend's best-effort contract. Successes refresh the
 *    parent drawer's mappings list; failures stay on screen so the
 *    operator can see what went wrong.
 *
 *  • The modal doesn't close on partial failure — the operator can fix
 *    names (e.g. tag-name conflicts) and re-import the failed rows.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  X, ChevronRight, ChevronDown, Folder, Gauge, AlertCircle,
  CheckCircle2, Loader2, Search, RefreshCw,
} from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { OpcSourceResponse } from "@/types/api";


// ── Backend shapes (must match opc_sources.py schemas) ───────────────

type NodeClass =
  | "Object" | "Variable" | "Method" | "View"
  | "DataType" | "ReferenceType" | "ObjectType" | "VariableType"
  | "Unspecified";

interface BrowseNode {
  node_id: string;
  browse_name: string;
  display_name: string;
  node_class: NodeClass;
  is_system: boolean;
  is_mapped: boolean;
  data_type: string | null;            // raw UA type, e.g. 'Double'
  induvista_data_type: string | null;  // mapped INDUVISTA type, e.g. 'float64'
}

interface BrowseResponse {
  parent_node_id: string;
  children: BrowseNode[];
}

interface BulkMappingItem {
  node_id: string;
  tag_name: string;
  data_type: string;
  tag_description?: string | null;
  engineering_unit?: string | null;
  decimal_places?: number | null;
}

interface BulkMappingResult {
  node_id: string;
  tag_name: string;
  success: boolean;
  mapping_id: number | null;
  error: string | null;
}

interface BulkMappingResponse {
  total: number;
  succeeded: number;
  failed: number;
  results: BulkMappingResult[];
}


// ── Selected-row state (one per ticked variable) ─────────────────────

interface SelectedRow {
  node_id: string;
  leaf_name: string;          // e.g. "KPW_CUR_DAILY_MASS"
  // auto-generated name *without* prefix — full computed name is
  // `${prefix}${derivedName(leaf_name)}`. Per-row override stored
  // separately so a prefix change still affects un-overridden rows.
  custom_name: string | null;  // null = use auto-derived
  data_type: string;            // INDUVISTA type; required for import
  engineering_unit: string;     // empty string = no unit
}

/** Convert a Kepware leaf browse_name into an INDUVISTA-friendly tag suffix.
 *  Lowercase, no spaces. Preserves underscores already in the name.
 *  Examples:
 *    "KPW_CUR_DAILY_MASS"  → "kpw_cur_daily_mass"
 *    "DescriptionTag"      → "descriptiontag"
 *    "Tag 1"               → "tag_1"
 */
function deriveTagSuffix(leaf: string): string {
  return leaf.toLowerCase().replace(/\s+/g, "_");
}

/** Compute the final tag name from the parts. Used both for live
 *  preview in the Selected pane and at submit time. */
function computeTagName(row: SelectedRow, prefix: string): string {
  if (row.custom_name !== null) return row.custom_name;
  return `${prefix}${deriveTagSuffix(row.leaf_name)}`;
}


// ── Tree-state shape (per-folder lazy cache) ─────────────────────────

interface FolderState {
  /** Loaded children of this folder. null = not loaded yet. */
  children: BrowseNode[] | null;
  /** True while the fetch is in flight. */
  loading: boolean;
  /** Error message from the last fetch attempt, if any. */
  error: string | null;
  /** Whether the folder is currently expanded in the UI. */
  expanded: boolean;
}

const emptyFolderState: FolderState = {
  children: null,
  loading: false,
  error: null,
  expanded: false,
};


// ── Component ────────────────────────────────────────────────────────

interface Props {
  open: boolean;
  source: OpcSourceResponse | null;
  onClose: () => void;
  /** Optional callback after a successful import — parent uses it to
   *  refresh its mappings list. */
  onImported?: () => void;
}

export function OpcBrowseImportModal({ open, source, onClose, onImported }: Props) {
  const qc = useQueryClient();

  // Per-folder cache keyed by node_id. Root key is "__root__" which
  // we translate to "ObjectsFolder" when calling the backend.
  const [folders, setFolders] = useState<Record<string, FolderState>>({});

  // Selection: keyed by node_id, stable across folder collapse/re-expand.
  const [selected, setSelected] = useState<Record<string, SelectedRow>>({});

  // Toolbar controls
  const [prefix, setPrefix] = useState("");
  const [filterText, setFilterText] = useState("");
  const [showSystem, setShowSystem] = useState(false);

  // Last bulk-import result (null until first import attempt).
  const [importResult, setImportResult] = useState<BulkMappingResponse | null>(null);

  // Reset everything when the modal opens fresh for a new source.
  useEffect(() => {
    if (open && source) {
      setFolders({});
      setSelected({});
      setPrefix("");
      setFilterText("");
      setImportResult(null);
      // Auto-expand the root so the operator sees content immediately.
      loadFolder("__root__", "ObjectsFolder");
    }
    // We intentionally don't depend on loadFolder — it closes over state
    // setters which are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, source?.id]);

  /** Fetch children of one node from the backend and store in `folders`.
   *  `folderKey` is what we store under (typically the parent node_id);
   *  `browseNodeId` is what we send to the backend (use 'ObjectsFolder'
   *  for the root). */
  async function loadFolder(folderKey: string, browseNodeId: string) {
    if (!source) return;
    setFolders((f) => ({
      ...f,
      [folderKey]: { ...(f[folderKey] ?? emptyFolderState), loading: true, error: null },
    }));
    try {
      const qs = browseNodeId === "ObjectsFolder"
        ? ""
        : `?node_id=${encodeURIComponent(browseNodeId)}`;
      const res = await api.get<BrowseResponse>(
        `/opc-sources/${source.id}/browse${qs}`,
      );
      setFolders((f) => ({
        ...f,
        [folderKey]: {
          children: res.children,
          loading: false,
          error: null,
          expanded: true,
        },
      }));
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e instanceof Error ? e.message : "unknown error");
      setFolders((f) => ({
        ...f,
        [folderKey]: {
          children: f[folderKey]?.children ?? null,
          loading: false,
          error: msg,
          expanded: true,  // keep expanded so the error is visible
        },
      }));
    }
  }

  /** Toggle a folder's expand state, lazy-loading children on first
   *  expand. */
  function toggleFolder(node: BrowseNode) {
    const key = node.node_id;
    const current = folders[key];
    if (!current || current.children === null) {
      // First expand — fetch.
      loadFolder(key, node.node_id);
      return;
    }
    // Already loaded — just toggle expanded.
    setFolders((f) => ({
      ...f,
      [key]: { ...current, expanded: !current.expanded },
    }));
  }

  /** Tick / untick a Variable node. */
  function toggleSelected(node: BrowseNode) {
    if (node.is_mapped) return;
    if (node.node_class !== "Variable") return;
    setSelected((s) => {
      if (s[node.node_id]) {
        // Untick — remove from selection.
        const next = { ...s };
        delete next[node.node_id];
        return next;
      }
      // Tick — add. Default data_type = induvista_data_type if backend
      // mapped it; otherwise empty (operator must choose before import).
      return {
        ...s,
        [node.node_id]: {
          node_id: node.node_id,
          leaf_name: node.browse_name,
          custom_name: null,
          data_type: node.induvista_data_type ?? "",
          engineering_unit: "",
        },
      };
    });
  }

  /** Edit a selected row in place (rename, change type, set unit). */
  function updateSelectedRow(node_id: string, patch: Partial<SelectedRow>) {
    setSelected((s) => {
      const cur = s[node_id];
      if (!cur) return s;
      return { ...s, [node_id]: { ...cur, ...patch } };
    });
  }

  function removeSelected(node_id: string) {
    setSelected((s) => {
      const next = { ...s };
      delete next[node_id];
      return next;
    });
  }

  // Computed: array form of `selected`, in insertion order.
  // Object property iteration order is insertion-order in JS for string
  // keys, which is good enough for UI display.
  const selectedRows = useMemo(() => Object.values(selected), [selected]);

  // Pre-check: any selected row without a data_type? Block import until
  // they're all set.
  const missingDataType = selectedRows.some((r) => !r.data_type);

  // Bulk import mutation. Posts the wrapped body and stores per-row
  // results so the operator sees what failed.
  const importMut = useMutation({
    mutationFn: async () => {
      if (!source) throw new Error("no source");
      const items: BulkMappingItem[] = selectedRows.map((r) => ({
        node_id: r.node_id,
        tag_name: computeTagName(r, prefix),
        data_type: r.data_type,
        engineering_unit: r.engineering_unit || null,
      }));
      return api.post<BulkMappingResponse>(
        `/opc-sources/${source.id}/mappings/bulk`,
        { items },
      );
    },
    onSuccess: (res) => {
      setImportResult(res);
      // Successes need to drop out of the Selected pane so they can't
      // be re-imported. Failures stay so the operator can fix and retry.
      if (res.succeeded > 0) {
        setSelected((s) => {
          const next = { ...s };
          for (const r of res.results) {
            if (r.success) delete next[r.node_id];
          }
          return next;
        });
        // Refresh parent caches that care about mapping changes.
        qc.invalidateQueries({ queryKey: ["opc-mappings", source?.id] });
        qc.invalidateQueries({ queryKey: ["opc-sources"] });
        onImported?.();
      }
      // Also refresh the browse cache for visible folders so the newly-
      // mapped rows show their badge without a full re-open.
      setFolders((f) => {
        const out: typeof f = { ...f };
        const successNodeIds = new Set(
          res.results.filter((r) => r.success).map((r) => r.node_id),
        );
        for (const k of Object.keys(out)) {
          const fs = out[k];
          if (!fs.children) continue;
          out[k] = {
            ...fs,
            children: fs.children.map((c) =>
              successNodeIds.has(c.node_id) ? { ...c, is_mapped: true } : c,
            ),
          };
        }
        return out;
      });
    },
  });

  if (!open || !source) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-stretch justify-end"
      onClick={(e) => {
        // click on backdrop closes — but only if NOT in the middle of an import
        if (e.target === e.currentTarget && !importMut.isPending) onClose();
      }}
    >
      <div
        className="bg-background h-full w-full max-w-[1400px] shadow-xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
        data-testid="opc-browse-modal"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <div>
            <h2 className="text-base font-semibold">
              Browse &amp; Import — <span className="font-mono">{source.name}</span>
            </h2>
            <p className="text-xs text-muted-foreground">
              Navigate the OPC server's address space; tick variables to import as INDUVISTA tags.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={importMut.isPending}
            className="h-8 w-8 inline-flex items-center justify-center rounded
                       hover:bg-secondary disabled:opacity-30"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-3 px-4 py-2 border-b bg-muted/20">
          <div className="space-y-1">
            <Label htmlFor="prefix" className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Prefix
            </Label>
            <Input
              id="prefix"
              value={prefix}
              onChange={(e) => setPrefix(e.target.value)}
              placeholder="e.g. kepware.c1."
              className="h-8 w-48 font-mono text-xs"
              data-testid="opc-browse-prefix"
            />
          </div>
          <div className="space-y-1 flex-1">
            <Label htmlFor="filter" className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Filter
            </Label>
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
              <Input
                id="filter"
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                placeholder="filter by name…"
                className="h-8 pl-7 text-xs"
                data-testid="opc-browse-filter"
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs mt-4 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={showSystem}
              onChange={(e) => setShowSystem(e.target.checked)}
              className="h-3.5 w-3.5"
              data-testid="opc-browse-show-system"
            />
            <span>Show system folders</span>
          </label>
          <button
            type="button"
            onClick={() => loadFolder("__root__", "ObjectsFolder")}
            className="text-xs px-2 py-1 mt-4 rounded border border-border
                       hover:bg-secondary inline-flex items-center gap-1.5"
            title="Re-fetch the address space"
          >
            <RefreshCw className={cn(
              "h-3 w-3",
              folders["__root__"]?.loading && "animate-spin",
            )} />
            Refresh
          </button>
        </div>

        {/* Main body: tree (left) + selection (right) */}
        <div className="flex-1 grid grid-cols-[1fr,400px] overflow-hidden">
          {/* Tree pane */}
          <div className="overflow-y-auto border-r p-2">
            <NodeList
              folderKey="__root__"
              folders={folders}
              showSystem={showSystem}
              filterText={filterText}
              selected={selected}
              onToggleFolder={toggleFolder}
              onToggleSelected={toggleSelected}
              depth={0}
            />
          </div>

          {/* Selection pane */}
          <div className="overflow-y-auto p-3 bg-muted/10">
            <div className="text-xs text-muted-foreground mb-2 flex items-center justify-between">
              <span>
                Selected ({selectedRows.length})
                {missingDataType && (
                  <span className="ml-2 text-amber-700">⚠ some types missing</span>
                )}
              </span>
              {selectedRows.length > 0 && (
                <button
                  type="button"
                  onClick={() => setSelected({})}
                  className="text-[10px] text-muted-foreground hover:text-foreground underline"
                >
                  Clear all
                </button>
              )}
            </div>
            {selectedRows.length === 0 ? (
              <div className="text-xs text-muted-foreground py-6 text-center italic">
                No tags selected. Tick checkboxes in the tree to add.
              </div>
            ) : (
              <div className="space-y-2">
                {selectedRows.map((r) => (
                  <SelectedRowCard
                    key={r.node_id}
                    row={r}
                    prefix={prefix}
                    onUpdate={(patch) => updateSelectedRow(r.node_id, patch)}
                    onRemove={() => removeSelected(r.node_id)}
                  />
                ))}
              </div>
            )}

            {/* Import result (after a bulk POST) */}
            {importResult && (
              <ImportResultPanel result={importResult} onDismiss={() => setImportResult(null)} />
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 px-4 py-3 border-t bg-background">
          <div className="text-xs text-muted-foreground">
            {selectedRows.length === 0
              ? "Tick variables in the tree on the left"
              : missingDataType
                ? "Set a data type for every selected row before importing"
                : `Ready to import ${selectedRows.length} tag${selectedRows.length === 1 ? "" : "s"}.`}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose} disabled={importMut.isPending}>
              Cancel
            </Button>
            <Button
              onClick={() => importMut.mutate()}
              disabled={selectedRows.length === 0 || missingDataType || importMut.isPending}
              data-testid="opc-browse-import-btn"
            >
              {importMut.isPending ? (
                <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" /> Importing…</>
              ) : (
                `Import ${selectedRows.length} tag${selectedRows.length === 1 ? "" : "s"}`
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}


// ── Recursive tree node list ─────────────────────────────────────────

interface NodeListProps {
  folderKey: string;
  folders: Record<string, FolderState>;
  showSystem: boolean;
  filterText: string;
  selected: Record<string, SelectedRow>;
  onToggleFolder: (n: BrowseNode) => void;
  onToggleSelected: (n: BrowseNode) => void;
  depth: number;
}

function NodeList({
  folderKey, folders, showSystem, filterText,
  selected, onToggleFolder, onToggleSelected, depth,
}: NodeListProps) {
  const fs = folders[folderKey] ?? emptyFolderState;

  if (fs.loading && !fs.children) {
    return (
      <div className="text-xs text-muted-foreground flex items-center gap-2 py-2" style={{ paddingLeft: depth * 16 }}>
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading…
      </div>
    );
  }
  if (fs.error) {
    return (
      <div className="text-xs text-destructive flex items-start gap-1.5 py-2" style={{ paddingLeft: depth * 16 }}>
        <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
        <span>{fs.error}</span>
      </div>
    );
  }
  if (!fs.children) {
    // Not yet loaded and not loading — shouldn't normally happen,
    // but defensive: just show nothing.
    return null;
  }

  const filterLower = filterText.trim().toLowerCase();
  const visible = fs.children.filter((c) => {
    if (!showSystem && c.is_system) return false;
    if (filterLower && !c.browse_name.toLowerCase().includes(filterLower)) {
      // For folders, allow them through so their children can match.
      // For Variables that don't match, hide.
      if (c.node_class === "Variable") return false;
    }
    return true;
  });

  if (visible.length === 0) {
    return (
      <div className="text-xs text-muted-foreground italic py-1" style={{ paddingLeft: depth * 16 }}>
        (no items)
      </div>
    );
  }

  return (
    <div>
      {visible.map((node) => (
        <NodeRow
          key={node.node_id}
          node={node}
          folders={folders}
          showSystem={showSystem}
          filterText={filterText}
          selected={selected}
          onToggleFolder={onToggleFolder}
          onToggleSelected={onToggleSelected}
          depth={depth}
        />
      ))}
    </div>
  );
}


// ── Single row (folder OR variable) ──────────────────────────────────

interface NodeRowProps {
  node: BrowseNode;
  folders: Record<string, FolderState>;
  showSystem: boolean;
  filterText: string;
  selected: Record<string, SelectedRow>;
  onToggleFolder: (n: BrowseNode) => void;
  onToggleSelected: (n: BrowseNode) => void;
  depth: number;
}

function NodeRow({
  node, folders, showSystem, filterText,
  selected, onToggleFolder, onToggleSelected, depth,
}: NodeRowProps) {
  const isVar = node.node_class === "Variable";
  const isObj = node.node_class === "Object";
  const fs = folders[node.node_id];
  const expanded = fs?.expanded === true;
  const isSelected = selected[node.node_id] !== undefined;

  return (
    <>
      <div
        className={cn(
          "flex items-center gap-1.5 py-1 px-1 rounded text-xs hover:bg-secondary/40 cursor-pointer",
          node.is_system && "opacity-60",
        )}
        style={{ paddingLeft: depth * 16 + 4 }}
        data-testid={
          isObj
            ? `opc-browse-folder-${node.browse_name}`
            : `opc-browse-row-${node.browse_name}`
        }
        onClick={() => {
          if (isObj) onToggleFolder(node);
          else if (isVar) onToggleSelected(node);
        }}
      >
        {/* Expand/collapse chevron or spacer */}
        {isObj ? (
          expanded
            ? <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />
            : <ChevronRight className="h-3 w-3 text-muted-foreground shrink-0" />
        ) : (
          <span className="w-3 shrink-0" />
        )}

        {/* Checkbox for variables */}
        {isVar && (
          <input
            type="checkbox"
            checked={isSelected}
            disabled={node.is_mapped}
            onChange={() => onToggleSelected(node)}
            onClick={(e) => e.stopPropagation()}
            className="h-3.5 w-3.5 shrink-0"
            data-testid={`opc-browse-checkbox-${node.browse_name}`}
          />
        )}

        {/* Icon */}
        {isObj ? (
          <Folder className="h-3.5 w-3.5 text-amber-600 shrink-0" />
        ) : (
          <Gauge className="h-3.5 w-3.5 text-blue-600 shrink-0" />
        )}

        {/* Name */}
        <span className={cn(
          "font-mono truncate",
          node.is_mapped && "text-muted-foreground",
        )}>
          {node.browse_name}
        </span>

        {/* Variable type + status badges */}
        {isVar && node.data_type && (
          <span className="text-[10px] text-muted-foreground ml-1">
            ({node.data_type})
          </span>
        )}
        {isVar && node.is_mapped && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200 inline-flex items-center gap-0.5 shrink-0">
            <CheckCircle2 className="h-2.5 w-2.5" /> mapped
          </span>
        )}
        {isVar && !node.induvista_data_type && !node.is_mapped && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200 shrink-0">
            unknown type
          </span>
        )}
      </div>

      {/* Children */}
      {isObj && expanded && (
        <NodeList
          folderKey={node.node_id}
          folders={folders}
          showSystem={showSystem}
          filterText={filterText}
          selected={selected}
          onToggleFolder={onToggleFolder}
          onToggleSelected={onToggleSelected}
          depth={depth + 1}
        />
      )}
    </>
  );
}


// ── Selected-row card (right pane) ───────────────────────────────────

const DATA_TYPE_OPTIONS = [
  "float64", "float32",
  "int16", "int32", "int64",
  "uint16", "uint32", "uint64",
  "bool", "string",
];

interface SelectedRowCardProps {
  row: SelectedRow;
  prefix: string;
  onUpdate: (patch: Partial<SelectedRow>) => void;
  onRemove: () => void;
}

function SelectedRowCard({ row, prefix, onUpdate, onRemove }: SelectedRowCardProps) {
  const computedName = computeTagName(row, prefix);
  const usingCustom = row.custom_name !== null;

  return (
    <div className="rounded border bg-background p-2 space-y-1.5 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="font-mono text-[11px] truncate text-muted-foreground" title={row.node_id}>
          {row.leaf_name}
        </div>
        <button
          type="button"
          onClick={onRemove}
          className="text-muted-foreground hover:text-destructive shrink-0"
          aria-label="Remove from selection"
          title="Remove"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
      <div>
        <Label className="text-[10px] text-muted-foreground">Tag name</Label>
        <Input
          value={computedName}
          onChange={(e) => onUpdate({ custom_name: e.target.value })}
          className="h-7 font-mono text-[11px]"
        />
        {usingCustom && (
          <button
            type="button"
            onClick={() => onUpdate({ custom_name: null })}
            className="text-[10px] text-muted-foreground hover:text-foreground underline mt-0.5"
          >
            Reset to auto-name
          </button>
        )}
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <div>
          <Label className="text-[10px] text-muted-foreground">Type</Label>
          <select
            value={row.data_type}
            onChange={(e) => onUpdate({ data_type: e.target.value })}
            className={cn(
              "h-7 w-full rounded border border-input bg-background px-2 text-[11px]",
              !row.data_type && "border-amber-300 bg-amber-50",
            )}
          >
            <option value="">— choose —</option>
            {DATA_TYPE_OPTIONS.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <Label className="text-[10px] text-muted-foreground">Unit</Label>
          <Input
            value={row.engineering_unit}
            onChange={(e) => onUpdate({ engineering_unit: e.target.value })}
            placeholder="optional"
            className="h-7 text-[11px]"
          />
        </div>
      </div>
    </div>
  );
}


// ── Import result panel ──────────────────────────────────────────────

function ImportResultPanel({ result, onDismiss }: { result: BulkMappingResponse; onDismiss: () => void }) {
  const failures = result.results.filter((r) => !r.success);
  return (
    <div className={cn(
      "mt-4 rounded border p-2.5 text-xs",
      result.failed === 0
        ? "bg-emerald-50/50 border-emerald-200"
        : "bg-amber-50/50 border-amber-200",
    )}>
      <div className="flex items-start justify-between mb-1.5">
        <div className="font-semibold">
          {result.failed === 0
            ? `✓ Imported ${result.succeeded} of ${result.total} tag${result.total === 1 ? "" : "s"}`
            : `Imported ${result.succeeded} of ${result.total}, ${result.failed} failed`}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="text-muted-foreground hover:text-foreground"
          aria-label="Dismiss"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
      {failures.length > 0 && (
        <div className="space-y-1 mt-2 max-h-48 overflow-y-auto">
          <div className="text-[10px] text-muted-foreground uppercase tracking-wide">Failed rows</div>
          {failures.map((f) => (
            <div key={f.node_id} className="border-l-2 border-amber-400 pl-2 py-0.5">
              <div className="font-mono text-[11px] truncate" title={f.node_id}>{f.tag_name}</div>
              <div className="text-[10px] text-destructive">{f.error}</div>
            </div>
          ))}
          <div className="text-[10px] text-muted-foreground mt-2 italic">
            Failed rows remain in the Selected pane — adjust names and re-import.
          </div>
        </div>
      )}
    </div>
  );
}
