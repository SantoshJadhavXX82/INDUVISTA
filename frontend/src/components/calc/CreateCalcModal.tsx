/**
 * Phase 16.0b - Create-calc modal.
 *
 * Output tag picker supports TWO modes (Phase 16.0b polish):
 *   "Calculated tag (internal)"  - inline-create a new tag on the
 *                                   Calculations device. Operator
 *                                   types a name; we POST /api/tags
 *                                   then use the returned id.
 *   "Existing tag"               - pick from the full tag list.
 *
 * Block-type picker is grouped by category. The form below it is
 * schema-driven (CalcBlockForm) so each block type renders its own
 * fields with no hardcoding.
 *
 * Submit:
 *   1. If "new" tag: POST /api/tags first to create it.
 *   2. POST /api/calc/definitions with the (new or existing) tag id.
 *   3. Invalidate query cache, close.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X, AlertTriangle, Loader2 } from "lucide-react";

import { useBlockTypes } from "@/lib/useCalcDefinitions";
import { useTagsList } from "@/lib/useTagsList";
import { CalcBlockForm } from "./CalcBlockForm";
import type { BlockConfigDraft } from "@/types/calcBlockSchemas";
import type { CalcDefinition } from "@/types/calcDefinitions";


interface CreateCalcModalProps {
  open: boolean;
  onClose: () => void;
  /** When provided, the modal opens in EDIT mode: pre-fills the form
   *  and PUTs instead of POSTing. Block type and output tag are not
   *  editable when editing (would orphan history or break referential
   *  integrity). To change those, delete and recreate. */
  existingCalc?: CalcDefinition | null;
}


const COMMON_RATES = [
  { ms: 100,   label: "100 ms (10 Hz)" },
  { ms: 250,   label: "250 ms (4 Hz)" },
  { ms: 500,   label: "500 ms (2 Hz)" },
  { ms: 1000,  label: "1 s" },
  { ms: 5000,  label: "5 s" },
  { ms: 10000, label: "10 s" },
  { ms: 30000, label: "30 s" },
  { ms: 60000, label: "1 min" },
];

const DATA_TYPES = [
  "float64", "float32",
  "int16", "uint16", "int32", "uint32", "int64", "uint64",
  "bool",
];


// Discover the Calculations pseudo-device once. It's the manual-protocol
// device that owns calc-output tags. Cached forever; only changes on
// schema migration.
interface DeviceListItem {
  id: number;
  name: string;
  protocol: string;
}

async function fetchDevices(): Promise<DeviceListItem[]> {
  const res = await fetch("/api/devices");
  if (!res.ok) throw new Error(`Failed to fetch devices: HTTP ${res.status}`);
  const data = await res.json();
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.devices)) return data.devices;
  if (Array.isArray(data?.items)) return data.items;
  return [];
}

function useCalculationsDevice() {
  return useQuery({
    queryKey: ["calculations-device"],
    queryFn: async () => {
      const devices = await fetchDevices();
      // Prefer protocol='manual' with name containing "calc" (case-insensitive).
      const found = devices.find((d) =>
        d.protocol === "manual" && /calc/i.test(d.name)
      );
      if (found) return found;
      // Fallback: any manual-protocol device.
      const anyManual = devices.find((d) => d.protocol === "manual");
      return anyManual ?? null;
    },
    staleTime: 24 * 60 * 60 * 1000,
  });
}


export function CreateCalcModal({ open, onClose, existingCalc }: CreateCalcModalProps) {
  const isEditMode = !!existingCalc;
  const types = useBlockTypes();
  const tags = useTagsList();
  const calcDevice = useCalculationsDevice();
  const qc = useQueryClient();

  const [blockCode, setBlockCode] = useState<string>("");
  const [blockConfig, setBlockConfig] = useState<BlockConfigDraft>({});
  const [rateMs, setRateMs] = useState<number>(1000);
  const [enabled, setEnabled] = useState<boolean>(true);

  // Output tag mode + state for each path
  const [outputMode, setOutputMode] = useState<"new" | "existing">("new");
  const [existingTagId, setExistingTagId] = useState<number | "">("");
  const [newTagName, setNewTagName] = useState<string>("");
  const [newTagDataType, setNewTagDataType] = useState<string>("float64");
  const [newTagDescription, setNewTagDescription] = useState<string>("");

  const [submitError, setSubmitError] = useState<string | null>(null);

  // Reset / pre-fill when modal opens or the edit subject changes.
  useEffect(() => {
    if (!open) {
      reset();
      return;
    }
    if (existingCalc) {
      // Edit mode: pre-fill from the def. Output tag editing is
      // disabled, so we put the existing tag in "existing" mode and
      // leave new-tag fields blank.
      setBlockCode(existingCalc.block_type);
      setBlockConfig({ ...existingCalc.block_config });
      setRateMs(existingCalc.execution_rate_ms);
      setEnabled(existingCalc.enabled);
      setOutputMode("existing");
      setExistingTagId(existingCalc.tag_id);
      setNewTagName("");
      setNewTagDataType("float64");
      setNewTagDescription("");
      setSubmitError(null);
    } else {
      reset();
    }
  }, [open, existingCalc]);

  // Group block types by category for the picker.
  const grouped = useMemo(() => {
    const out = new Map<string, { code: string; label: string }[]>();
    for (const t of types.data ?? []) {
      if (!t.is_evaluable) continue;
      if (!out.has(t.category)) out.set(t.category, []);
      out.get(t.category)!.push({ code: t.code, label: t.label });
    }
    for (const list of out.values()) {
      list.sort((a, b) => a.label.localeCompare(b.label));
    }
    return Array.from(out.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [types.data]);

  // Existing-tag dropdown - sorted by device, then name.
  const sortedTags = useMemo(() => {
    const all = tags.data ?? [];
    return [...all].sort((a, b) => {
      if (a.device_id !== b.device_id) return a.device_id - b.device_id;
      return a.name.localeCompare(b.name);
    });
  }, [tags.data]);

  // Suggest a tag name from block type if user hasn't typed one yet.
  useEffect(() => {
    if (outputMode === "new" && !newTagName && blockCode) {
      const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
      setNewTagName(`${blockCode}_OUT_${ts}`);
    }
  }, [blockCode, outputMode]);  // intentional: don't reset name if already edited

  const mutation = useMutation({
    mutationFn: async () => {
      // EDIT MODE: just PUT the existing def with updated fields.
      if (isEditMode && existingCalc) {
        const calcBody = {
          tag_id: existingCalc.tag_id,           // unchanged
          block_type: existingCalc.block_type,   // unchanged
          block_config: blockConfig,
          execution_rate_ms: rateMs,
          enabled,
        };
        const res = await fetch(`/api/calc/definitions/${existingCalc.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(calcBody),
        });
        if (!res.ok) {
          let detail = "";
          try {
            const errBody = await res.json();
            detail = typeof errBody === "string" ? errBody : JSON.stringify(errBody);
          } catch {
            detail = await res.text();
          }
          throw new Error(`Update failed (HTTP ${res.status}): ${detail || "(no body)"}`);
        }
        return res.json();
      }

      // CREATE MODE: optionally create the output tag first, then create
      // the calc def.
      let tagId: number;

      // Step 1: if new-tag mode, POST /api/calc/output-tags first.
      // This endpoint is purpose-built for calc-output tags - it
      // auto-detects the Calculations device and fills in any
      // Modbus-specific NOT NULL columns with safe defaults.
      if (outputMode === "new") {
        const tagBody = {
          name: newTagName.trim(),
          data_type: newTagDataType,
          description: newTagDescription.trim() || `Output of ${blockCode}`,
        };
        const tagRes = await fetch("/api/calc/output-tags", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(tagBody),
        });
        if (!tagRes.ok) {
          let detail = "";
          try {
            const errBody = await tagRes.json();
            detail = typeof errBody === "string" ? errBody : JSON.stringify(errBody);
          } catch {
            detail = await tagRes.text();
          }
          throw new Error(
            `Failed to create output tag (HTTP ${tagRes.status}): ${detail || "(no body)"}`
          );
        }
        const created = await tagRes.json();
        tagId = created.id;
        // Invalidate tags list so picker shows the new tag.
        qc.invalidateQueries({ queryKey: ["tags-list"] });
      } else {
        if (existingTagId === "" || existingTagId == null) {
          throw new Error("Select an existing tag.");
        }
        tagId = existingTagId as number;
      }

      // Step 2: POST /api/calc/definitions.
      const calcBody = {
        tag_id: tagId,
        block_type: blockCode,
        block_config: blockConfig,
        execution_rate_ms: rateMs,
        enabled,
      };
      const calcRes = await fetch("/api/calc/definitions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(calcBody),
      });
      if (!calcRes.ok) {
        let detail = "";
        try {
          const errBody = await calcRes.json();
          detail = typeof errBody === "string" ? errBody : JSON.stringify(errBody);
        } catch {
          detail = await calcRes.text();
        }
        throw new Error(
          `Failed to create calc (HTTP ${calcRes.status}): ${detail || "(no body)"}`
        );
      }
      return calcRes.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calc-definitions"] });
      reset();
      onClose();
    },
    onError: (err: Error) => setSubmitError(err.message),
  });

  function reset() {
    setBlockCode("");
    setBlockConfig({});
    setRateMs(1000);
    setEnabled(true);
    setOutputMode("new");
    setExistingTagId("");
    setNewTagName("");
    setNewTagDataType("float64");
    setNewTagDescription("");
    setSubmitError(null);
  }

  function handleClose() {
    if (mutation.isPending) return;
    onClose();
  }

  function handleSubmit() {
    setSubmitError(null);
    if (!blockCode) { setSubmitError("Select a block type."); return; }
    if (!isEditMode) {
      if (outputMode === "new" && !newTagName.trim()) {
        setSubmitError("Enter a name for the new calculated tag.");
        return;
      }
      if (outputMode === "existing" && (existingTagId === "" || existingTagId == null)) {
        setSubmitError("Select an existing output tag.");
        return;
      }
    }
    mutation.mutate();
  }

  function handleBlockCodeChange(newCode: string) {
    setBlockCode(newCode);
    setBlockConfig({});
    setSubmitError(null);
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center
                 bg-black/40 backdrop-blur-sm overflow-y-auto py-10"
      onClick={handleClose}
    >
      <div
        className="bg-card border border-border rounded shadow-lg
                   w-full max-w-2xl mx-4 my-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h2 className="text-sm font-medium">
            {isEditMode ? `Edit Calculation #${existingCalc?.id}` : "New Calculation"}
          </h2>
          <button
            type="button"
            onClick={handleClose}
            disabled={mutation.isPending}
            className="h-7 w-7 inline-flex items-center justify-center rounded
                       text-muted-foreground hover:bg-secondary disabled:opacity-30"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-4">
          {/* 1. Block-type picker */}
          <div>
            <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
              Block type <span className="text-destructive">*</span>
              {isEditMode && (
                <span className="ml-2 text-muted-foreground normal-case tracking-normal">
                  (not editable - delete and recreate to change)
                </span>
              )}
            </label>
            <select
              className="h-7 text-xs bg-card border border-border rounded px-2 w-full
                         disabled:opacity-60 disabled:cursor-not-allowed"
              value={blockCode}
              disabled={isEditMode}
              onChange={(e) => handleBlockCodeChange(e.target.value)}
            >
              <option value="">— select block type —</option>
              {grouped.map(([category, items]) => (
                <optgroup key={category} label={category}>
                  {items.map((t) => (
                    <option key={t.code} value={t.code}>
                      {t.label} ({t.code})
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          {/* 2. Schema-driven config form */}
          {blockCode && (
            <div className="border border-border rounded p-3 bg-secondary/10">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
                Block configuration
              </div>
              <CalcBlockForm
                blockCode={blockCode}
                blockConfig={blockConfig}
                onChange={setBlockConfig}
              />
            </div>
          )}

          {/* 3. Output tag (universal: any block can output to a new calc tag
                 OR an existing tag). In EDIT mode, output tag is locked. */}
          <div className="border border-border rounded p-3 bg-secondary/10">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
              Output tag
              {isEditMode && (
                <span className="ml-2 normal-case tracking-normal">
                  (locked - delete and recreate to change)
                </span>
              )}
            </div>

            {isEditMode ? (
              <div className="text-xs px-2 py-1.5 bg-card border border-border rounded">
                {(() => {
                  const tag = sortedTags.find((t) => t.id === existingTagId);
                  return tag
                    ? `${tag.name} (${tag.data_type}, device #${tag.device_id}, tag #${tag.id})`
                    : `tag #${existingTagId}`;
                })()}
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-1 mb-2 text-[11px]">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      checked={outputMode === "new"}
                      onChange={() => setOutputMode("new")}
                      className="cursor-pointer"
                    />
                    <span>Calculated tag (new, internal on Calculations device)</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      checked={outputMode === "existing"}
                      onChange={() => setOutputMode("existing")}
                      className="cursor-pointer"
                    />
                    <span>Existing tag</span>
                  </label>
                </div>

            {outputMode === "new" ? (
              <div className="ml-4 pl-3 border-l-2 border-border space-y-2">
                <div>
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                    Tag name <span className="text-destructive">*</span>
                  </label>
                  <input
                    type="text"
                    className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                    value={newTagName}
                    onChange={(e) => setNewTagName(e.target.value)}
                    placeholder="e.g. SUM_FlowRates_Hourly"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                      Data type
                    </label>
                    <select
                      className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                      value={newTagDataType}
                      onChange={(e) => setNewTagDataType(e.target.value)}
                    >
                      {DATA_TYPES.map((t) => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                      Owner device
                    </label>
                    <div className="h-7 text-xs px-2 py-1.5 border border-dashed border-border rounded text-muted-foreground">
                      {calcDevice.isLoading
                        ? "Detecting..."
                        : calcDevice.data
                          ? `${calcDevice.data.name} (#${calcDevice.data.id})`
                          : "no manual-protocol device found"}
                    </div>
                  </div>
                </div>
                <div>
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                    Description (optional)
                  </label>
                  <input
                    type="text"
                    className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                    value={newTagDescription}
                    onChange={(e) => setNewTagDescription(e.target.value)}
                    placeholder="What does this calc compute?"
                  />
                </div>
              </div>
            ) : (
              <div className="ml-4 pl-3 border-l-2 border-border">
                <select
                  className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                  value={existingTagId === "" ? "" : String(existingTagId)}
                  onChange={(e) => {
                    const v = e.target.value;
                    setExistingTagId(v === "" ? "" : Number(v));
                  }}
                >
                  <option value="">— select existing tag —</option>
                  {sortedTags.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} ({t.data_type}, device #{t.device_id}, tag #{t.id})
                    </option>
                  ))}
                </select>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Pick any existing tag. The calc will write to it on every tick.
                  Only one calc can target a tag at a time.
                </p>
              </div>
            )}
              </>
            )}
          </div>

          {/* 4. Rate + enabled */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                Execution rate <span className="text-destructive">*</span>
              </label>
              <select
                className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                value={rateMs}
                onChange={(e) => setRateMs(Number(e.target.value))}
              >
                {COMMON_RATES.map((r) => (
                  <option key={r.ms} value={r.ms}>{r.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                Enabled
              </label>
              <label className="flex items-center gap-2 h-7 cursor-pointer">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                />
                <span className="text-xs">{enabled ? "On" : "Off"}</span>
              </label>
            </div>
          </div>

          {/* Live preview of what'll be POSTed */}
          {blockCode && (
            <details className="text-[10px] text-muted-foreground">
              <summary className="cursor-pointer hover:text-foreground">
                Preview request body
              </summary>
              <pre className="mt-1 p-2 bg-secondary/30 rounded font-mono text-[10px] overflow-x-auto">
{outputMode === "new" ?
`# Step 1 - POST /api/calc/output-tags
${JSON.stringify({
  name: newTagName,
  data_type: newTagDataType,
  description: newTagDescription || `Output of ${blockCode}`,
}, null, 2)}
# (server auto-detects the Calculations device: ${
  calcDevice.data ? `${calcDevice.data.name} #${calcDevice.data.id}` : "(not yet detected)"
})

# Step 2 - POST /api/calc/definitions (using returned tag id)
${JSON.stringify({
  tag_id: "<from step 1>",
  block_type: blockCode,
  block_config: blockConfig,
  execution_rate_ms: rateMs,
  enabled,
}, null, 2)}` :
`# POST /api/calc/definitions
${JSON.stringify({
  tag_id: existingTagId === "" ? null : existingTagId,
  block_type: blockCode,
  block_config: blockConfig,
  execution_rate_ms: rateMs,
  enabled,
}, null, 2)}`}
              </pre>
            </details>
          )}

          {/* Error */}
          {submitError && (
            <div className="flex items-start gap-2 text-xs text-destructive
                            bg-destructive/10 border border-destructive/30
                            rounded p-2">
              <AlertTriangle className="h-3 w-3 flex-shrink-0 mt-0.5" />
              <span className="font-mono whitespace-pre-wrap break-words">{submitError}</span>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border bg-secondary/10">
          <button
            type="button"
            onClick={handleClose}
            disabled={mutation.isPending}
            className="text-xs px-3 py-1.5 rounded border border-border
                       hover:bg-secondary disabled:opacity-30"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={mutation.isPending || !blockCode}
            className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground
                       hover:bg-primary/90 disabled:opacity-30 inline-flex items-center gap-2"
          >
            {mutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            {isEditMode ? "Save changes" : "Create calc"}
          </button>
        </div>
      </div>
    </div>
  );
}
