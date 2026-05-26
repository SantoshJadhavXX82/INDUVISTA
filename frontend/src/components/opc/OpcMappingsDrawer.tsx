/**
 * OPC mappings drawer — Phase OPC-web.3.
 *
 * Slides in from the right when a row's "Mappings" action is clicked.
 * Shows the list of NodeId→tag_id mappings for one source, with an
 * inline "+ Add mapping" form that creates both the OPC mapping row
 * AND the underlying INDUVISTA tag in one POST.
 *
 * Mapping creation is intentionally minimal — name, NodeId, data_type,
 * decimal places, EU. The auto-created tag uses sensible defaults for
 * the Modbus-only columns (function_code=3, address=0 — both ignored
 * by the OPC supervisor). For richer tag configuration (alarms,
 * groups, named sets), the operator edits the tag in the Tags page
 * after creation.
 *
 * Delete operation drops the mapping AND the tag (via the FK CASCADE
 * on opc_tag_mappings.tag_id). This matches the contract documented
 * in the migration — OPC-only tags can't outlive their mapping.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X, Plus, Trash2, AlertTriangle, Loader2, Copy, Check, FolderTree } from "lucide-react";

import { api } from "@/lib/api";
import type {
  OpcMappingCreate, OpcMappingResponse, OpcSourceResponse,
} from "@/types/api";
import { OPC_SOURCES_QUERY_KEY } from "./CreateOpcSourceModal";
import { OpcBrowseImportModal } from "./OpcBrowseImportModal";
import { ConfirmDialog } from "@/components/ConfirmDialog";

const DATA_TYPES = [
  "float64", "float32",
  "int64", "uint64", "int32", "uint32", "int16", "uint16",
  "bool", "string",
];

interface Props {
  open: boolean;
  onClose: () => void;
  source: OpcSourceResponse | null;
}

export function OpcMappingsDrawer({ open, onClose, source }: Props) {
  const qc = useQueryClient();

  const mappingsQuery = useQuery({
    queryKey: ["opc-mappings", source?.id],
    queryFn: () => api.get<OpcMappingResponse[]>(
      `/opc-sources/${source!.id}/mappings`
    ),
    enabled: open && !!source,
  });

  // Add-mapping form state
  const [nodeId, setNodeId] = useState("");
  const [tagName, setTagName] = useState("");
  const [dataType, setDataType] = useState("float64");
  const [decimalPlaces, setDecimalPlaces] = useState<number | "">("");
  const [engineeringUnit, setEngineeringUnit] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const [pendingDelete, setPendingDelete] = useState<OpcMappingResponse | null>(null);
  const [copiedNodeId, setCopiedNodeId] = useState<number | null>(null);
  const [browseOpen, setBrowseOpen] = useState(false);

  // Esc to close.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Reset form when drawer reopens for a different source.
  useEffect(() => {
    if (open) {
      setNodeId("");
      setTagName("");
      setDataType("float64");
      setDecimalPlaces("");
      setEngineeringUnit("");
      setFormError(null);
    }
  }, [open, source?.id]);

  const addMutation = useMutation({
    mutationFn: async () => {
      const body: OpcMappingCreate = {
        node_id: nodeId.trim(),
        tag_name: tagName.trim(),
        data_type: dataType,
        decimal_places: decimalPlaces === "" ? null : Number(decimalPlaces),
        engineering_unit: engineeringUnit || null,
      };
      return api.post<OpcMappingResponse>(
        `/opc-sources/${source!.id}/mappings`,
        body,
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opc-mappings", source?.id] });
      qc.invalidateQueries({ queryKey: OPC_SOURCES_QUERY_KEY });  // refresh count
      setNodeId("");
      setTagName("");
      setEngineeringUnit("");
      setDecimalPlaces("");
      setFormError(null);
    },
    onError: (err: Error) => setFormError(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: (mapping: OpcMappingResponse) =>
      api.delete(`/opc-sources/${source!.id}/mappings/${mapping.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opc-mappings", source?.id] });
      qc.invalidateQueries({ queryKey: OPC_SOURCES_QUERY_KEY });
      setPendingDelete(null);
    },
  });

  if (!open || !source) return null;

  const canAdd =
    !addMutation.isPending &&
    nodeId.trim().length > 0 &&
    tagName.trim().length > 0;

  const copyNodeId = async (mapping: OpcMappingResponse) => {
    try {
      await navigator.clipboard.writeText(mapping.node_id);
      setCopiedNodeId(mapping.id);
      window.setTimeout(() => setCopiedNodeId(null), 1500);
    } catch {
      // Clipboard API can fail on insecure contexts; silently ignore.
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/30 backdrop-blur-[1px]"
        onClick={onClose}
        role="presentation"
      />

      {/* Slide-in panel */}
      <aside
        className="fixed top-0 right-0 z-50 h-full w-full max-w-lg
                   bg-card border-l border-border shadow-2xl
                   flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-labelledby="opc-mappings-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div>
            <h2 id="opc-mappings-title" className="text-sm font-medium">
              Mappings: <span className="font-mono">{source.name}</span>
            </h2>
            <p className="text-[10px] text-muted-foreground mt-0.5 font-mono">
              {source.endpoint}
            </p>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setBrowseOpen(true)}
              data-testid="opc-browse-open-btn"
              className="text-xs px-2 py-1 rounded border border-border
                         hover:bg-secondary inline-flex items-center gap-1.5"
              title="Browse the OPC address space and bulk-import variables as tags"
            >
              <FolderTree className="h-3 w-3" />
              Browse &amp; Import
            </button>
            <button
              type="button"
              onClick={onClose}
              className="h-6 w-6 inline-flex items-center justify-center rounded
                         text-muted-foreground hover:bg-secondary"
              aria-label="Close"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* Content (scrolls) */}
        <div className="flex-1 overflow-y-auto">
          {/* Existing mappings */}
          <div className="px-4 py-3 border-b border-border">
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-2">
              Existing mappings ({mappingsQuery.data?.length ?? 0})
            </p>

            {mappingsQuery.isLoading && (
              <div className="text-xs text-muted-foreground flex items-center gap-1.5">
                <Loader2 className="h-3 w-3 animate-spin" /> Loading…
              </div>
            )}

            {mappingsQuery.isError && (
              <div className="text-xs text-destructive">
                Failed to load mappings.
              </div>
            )}

            {mappingsQuery.data && mappingsQuery.data.length === 0 && (
              <div className="text-xs text-muted-foreground italic py-6 text-center">
                No mappings yet. Add one below to start subscribing
                to nodes on this server.
              </div>
            )}

            {mappingsQuery.data && mappingsQuery.data.length > 0 && (
              <div className="space-y-1.5">
                {mappingsQuery.data.map((m) => (
                  <div
                    key={m.id}
                    className="group flex items-start justify-between gap-2 px-2 py-1.5
                               border border-border rounded bg-secondary/20
                               hover:bg-secondary/40 transition-colors"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <span className="text-xs font-medium truncate">{m.tag_name}</span>
                        <span className="text-[10px] text-muted-foreground bg-card px-1 rounded">
                          {m.data_type}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 mt-0.5">
                        <code className="text-[10px] text-muted-foreground font-mono truncate">
                          {m.node_id}
                        </code>
                        <button
                          type="button"
                          onClick={() => copyNodeId(m)}
                          className="opacity-0 group-hover:opacity-100 transition-opacity
                                     h-4 w-4 inline-flex items-center justify-center rounded
                                     text-muted-foreground hover:text-foreground hover:bg-card"
                          aria-label="Copy NodeId"
                        >
                          {copiedNodeId === m.id
                            ? <Check className="h-2.5 w-2.5 text-emerald-600" />
                            : <Copy className="h-2.5 w-2.5" />}
                        </button>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setPendingDelete(m)}
                      className="opacity-0 group-hover:opacity-100 transition-opacity
                                 h-6 w-6 inline-flex items-center justify-center rounded
                                 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                      aria-label="Delete mapping"
                      title="Delete mapping and its tag"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Add mapping form */}
          <div className="px-4 py-3">
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-2">
              Add a mapping
            </p>
            <p className="text-[10px] text-muted-foreground mb-3 leading-relaxed">
              Creates an INDUVISTA tag (bound to this source's synthetic
              device) and links it to the OPC NodeId you specify. Once
              the worker resubscribes, samples start flowing.
            </p>

            <div className="space-y-2">
              <div>
                <label className="text-[10px] text-muted-foreground block mb-1">
                  OPC NodeId <span className="text-destructive">*</span>
                </label>
                <input
                  type="text"
                  value={nodeId}
                  onChange={(e) => setNodeId(e.target.value)}
                  placeholder="ns=1;s=DoubleValue"
                  className="h-8 text-xs font-mono bg-card border border-border rounded px-2 w-full
                             focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>

              <div>
                <label className="text-[10px] text-muted-foreground block mb-1">
                  INDUVISTA tag name <span className="text-destructive">*</span>
                </label>
                <input
                  type="text"
                  value={tagName}
                  onChange={(e) => setTagName(e.target.value)}
                  placeholder="OPC.MyTag"
                  className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                             focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div className="col-span-1">
                  <label className="text-[10px] text-muted-foreground block mb-1">
                    Data type
                  </label>
                  <select
                    value={dataType}
                    onChange={(e) => setDataType(e.target.value)}
                    className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                               focus:outline-none focus:ring-2 focus:ring-primary"
                  >
                    {DATA_TYPES.map((dt) => (
                      <option key={dt} value={dt}>{dt}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-muted-foreground block mb-1">
                    Decimal places
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={15}
                    value={decimalPlaces}
                    onChange={(e) => {
                      const v = e.target.value;
                      setDecimalPlaces(v === "" ? "" : Number(v));
                    }}
                    placeholder="auto"
                    className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                               focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-muted-foreground block mb-1">
                    Eng. unit
                  </label>
                  <input
                    type="text"
                    value={engineeringUnit}
                    onChange={(e) => setEngineeringUnit(e.target.value)}
                    placeholder="—"
                    maxLength={32}
                    className="h-8 text-xs bg-card border border-border rounded px-2 w-full
                               focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>
              </div>

              {formError && (
                <div className="flex items-start gap-2 text-[11px] text-destructive
                                bg-destructive/10 border border-destructive/30 rounded px-2 py-1.5">
                  <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                  <div>{formError}</div>
                </div>
              )}

              <button
                type="button"
                onClick={() => addMutation.mutate()}
                disabled={!canAdd}
                className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground
                           hover:bg-primary/90 disabled:opacity-30 disabled:cursor-not-allowed
                           inline-flex items-center gap-1.5 w-full justify-center"
              >
                {addMutation.isPending
                  ? <Loader2 className="h-3 w-3 animate-spin" />
                  : <Plus className="h-3 w-3" />}
                Add mapping
              </button>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-border bg-secondary/10 text-[10px] text-muted-foreground">
          {/* Phase OPC-web.2.1 — the worker now picks up mapping changes
              automatically within ~30s (its config_reloader poll interval).
              No manual restart needed; this message used to read
              "docker compose restart opc_worker". */}
          Mapping changes are picked up automatically by the OPC worker
          within ~30 seconds.
        </div>
      </aside>

      {/* Per-mapping delete confirmation */}
      <ConfirmDialog
        open={!!pendingDelete}
        title="Delete mapping?"
        description={
          pendingDelete ? (
            <>
              This will delete <b>{pendingDelete.tag_name}</b> (tag id{" "}
              {pendingDelete.tag_id}) along with all of its historical
              tag_values. To stop sampling without losing data, disable
              the source instead.
            </>
          ) : null
        }
        confirmLabel="Delete mapping + tag"
        severity="destructive"
        busy={deleteMutation.isPending}
        onConfirm={() => pendingDelete && deleteMutation.mutate(pendingDelete)}
        onCancel={() => setPendingDelete(null)}
      />

      {/* Browse + bulk-import modal */}
      <OpcBrowseImportModal
        open={browseOpen}
        source={source}
        onClose={() => setBrowseOpen(false)}
        onImported={() => {
          // Refresh this drawer's mappings list — the bulk import
          // adds rows that should appear immediately.
          mappingsQuery.refetch();
        }}
      />
    </>
  );
}
