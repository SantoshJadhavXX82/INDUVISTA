/**
 * Phase 17.0b - Create / edit Computed Tag modal with dual output mode.
 *
 * Adds the "Output target" radio: Internal (default, write to own anchor)
 * or External (write to a chosen existing tag elsewhere). The external
 * tag picker is sourced from useExternalOutputCandidates which filters
 * out computed-device tags and tags already taken by another calc.
 *
 * Edit mode: pre-fills output mode from existingCalc.output_tag_id.
 * Switching the radio mid-edit is allowed - submit will PATCH the
 * output_tag_id field to its new value (or NULL).
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X, AlertTriangle, Loader2, Settings, Plus, ArrowRight } from "lucide-react";

import {
  useBlockTypes, useComputedDevices, useExternalOutputCandidates,
  CALC_DEFINITIONS_QUERY_KEY,
} from "@/lib/useCalcDefinitions";
import { CalcBlockForm } from "./CalcBlockForm";
import { ComputedDevicesModal } from "./ComputedDevicesModal";
import type { BlockConfigDraft } from "@/types/calcBlockSchemas";
import type { CalcDefinition } from "@/types/calcDefinitions";


interface CreateCalcModalProps {
  open: boolean;
  onClose: () => void;
  /** Edit mode when set. Block type and tag metadata locked. */
  existingCalc?: CalcDefinition | null;
  /** Pre-select this device when opening in create mode. */
  initialDeviceId?: number | null;
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


type OutputMode = "internal" | "external";


export function CreateCalcModal({
  open, onClose, existingCalc, initialDeviceId,
}: CreateCalcModalProps) {
  const isEditMode = !!existingCalc;
  const types = useBlockTypes();
  const devices = useComputedDevices();
  const qc = useQueryClient();

  // Form state
  const [deviceId, setDeviceId] = useState<number | "">("");
  const [tagName, setTagName] = useState("");
  const [dataType, setDataType] = useState("float64");
  const [description, setDescription] = useState("");
  const [blockCode, setBlockCode] = useState<string>("");
  const [blockConfig, setBlockConfig] = useState<BlockConfigDraft>({});
  const [rateMs, setRateMs] = useState<number>(1000);
  const [enabled, setEnabled] = useState<boolean>(true);

  // Phase 17.0b - output target state
  const [outputMode, setOutputMode] = useState<OutputMode>("internal");
  const [outputTagId, setOutputTagId] = useState<number | "">("");

  // External output candidates (filtered to non-computed-device tags
  // not already taken by another calc). Exclude this calc's own ID so
  // its current target stays selectable in edit mode.
  const outputCandidates = useExternalOutputCandidates(existingCalc?.id);

  const [submitError, setSubmitError] = useState<string | null>(null);
  const [devicesModalOpen, setDevicesModalOpen] = useState(false);

  useEffect(() => {
    if (!open) {
      reset();
      return;
    }
    if (existingCalc) {
      setDeviceId(existingCalc.device_id);
      setTagName(existingCalc.name);
      setDataType(existingCalc.data_type);
      setDescription(existingCalc.description ?? "");
      setBlockCode(existingCalc.block_type);
      setBlockConfig({ ...existingCalc.block_config });
      setRateMs(existingCalc.execution_rate_ms);
      setEnabled(existingCalc.enabled);
      // Phase 17.0b - hydrate output mode from existing calc
      if (existingCalc.output_tag_id != null) {
        setOutputMode("external");
        setOutputTagId(existingCalc.output_tag_id);
      } else {
        setOutputMode("internal");
        setOutputTagId("");
      }
      setSubmitError(null);
    } else {
      reset();
      if (initialDeviceId != null) {
        setDeviceId(initialDeviceId);
      }
    }
  }, [open, existingCalc, initialDeviceId]);

  function reset() {
    setDeviceId("");
    setTagName("");
    setDataType("float64");
    setDescription("");
    setBlockCode("");
    setBlockConfig({});
    setRateMs(1000);
    setEnabled(true);
    setOutputMode("internal");
    setOutputTagId("");
    setSubmitError(null);
  }

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

  useEffect(() => {
    if (!isEditMode && !tagName && blockCode) {
      const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
      setTagName(`${blockCode}_OUT_${ts}`);
    }
  }, [blockCode, isEditMode]);  // eslint-disable-line react-hooks/exhaustive-deps

  /** Resolve the output_tag_id value to send to the backend. */
  function resolveOutputTagId(): number | null {
    if (outputMode === "internal") return null;
    return outputTagId === "" ? null : Number(outputTagId);
  }

  const mutation = useMutation({
    mutationFn: async () => {
      const output_tag_id = resolveOutputTagId();

      if (isEditMode && existingCalc) {
        // PATCH only what's editable. Include output_tag_id always
        // (so switching from external back to internal works - we
        // explicitly send null).
        const body = {
          description: description.trim() || null,
          block_config: blockConfig,
          execution_rate_ms: rateMs,
          enabled,
          output_tag_id,
        };
        const res = await fetch(`/api/computed-tags/${existingCalc.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          let detail = "";
          try {
            const errBody = await res.json();
            detail = typeof errBody === "string"
              ? errBody
              : (errBody.detail ?? JSON.stringify(errBody));
          } catch { detail = await res.text(); }
          throw new Error(`Update failed (HTTP ${res.status}): ${detail || "(no body)"}`);
        }
        return res.json();
      }

      // CREATE: composite POST /api/computed-tags
      const body = {
        device_id: deviceId,
        name: tagName.trim(),
        data_type: dataType,
        description: description.trim() || null,
        block_type: blockCode,
        block_config: blockConfig,
        execution_rate_ms: rateMs,
        enabled,
        output_tag_id,
      };
      const res = await fetch("/api/computed-tags", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        let detail = "";
        try {
          const errBody = await res.json();
          detail = typeof errBody === "string"
            ? errBody
            : (errBody.detail ?? JSON.stringify(errBody));
        } catch { detail = await res.text(); }
        throw new Error(`Create failed (HTTP ${res.status}): ${detail || "(no body)"}`);
      }
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: CALC_DEFINITIONS_QUERY_KEY });
      qc.invalidateQueries({ queryKey: ["computed-devices"] });
      reset();
      onClose();
    },
    onError: (err: Error) => setSubmitError(err.message),
  });

  function handleClose() {
    if (mutation.isPending) return;
    onClose();
  }

  function handleSubmit() {
    setSubmitError(null);
    if (!isEditMode) {
      if (deviceId === "" || deviceId == null) {
        setSubmitError("Pick a Computed Device.");
        return;
      }
      if (!tagName.trim()) {
        setSubmitError("Enter a tag name.");
        return;
      }
    }
    if (!blockCode) {
      setSubmitError("Select a block type.");
      return;
    }
    // Phase 17.0b - validate external output
    if (outputMode === "external" && (outputTagId === "" || outputTagId == null)) {
      setSubmitError("Select an external output tag, or switch back to Internal.");
      return;
    }
    mutation.mutate();
  }

  function handleBlockCodeChange(newCode: string) {
    setBlockCode(newCode);
    setBlockConfig({});
    setSubmitError(null);
  }

  function handleOutputModeChange(mode: OutputMode) {
    setOutputMode(mode);
    if (mode === "internal") {
      setOutputTagId("");
    }
    setSubmitError(null);
  }

  const enabledDevices = useMemo(
    () => (devices.data ?? []).filter((d) => d.enabled),
    [devices.data],
  );

  // Currently selected external candidate (for the data_type warning)
  const selectedOutputCandidate = useMemo(() => {
    if (outputMode !== "external" || outputTagId === "") return null;
    return outputCandidates.data?.find((c) => c.id === outputTagId) ?? null;
  }, [outputMode, outputTagId, outputCandidates.data]);

  if (!open) return null;

  return (
    <>
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
              {isEditMode ? `Edit Computed Tag #${existingCalc?.id}` : "New Computed Tag"}
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

          <div className="p-4 space-y-4">
            {/* Empty state */}
            {!isEditMode && !devices.isLoading && enabledDevices.length === 0 ? (
              <div className="border border-amber-300 bg-amber-50 rounded p-4">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="h-4 w-4 text-amber-700 flex-shrink-0 mt-0.5" />
                  <div className="text-xs text-amber-900 space-y-2">
                    <div className="font-medium">No Computed Devices yet</div>
                    <p>
                      A computed tag must live on a Computed Device. Create one first,
                      then come back to add tags to it.
                    </p>
                    <button
                      type="button"
                      onClick={() => setDevicesModalOpen(true)}
                      className="text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground
                                 hover:bg-primary/90 inline-flex items-center gap-1.5"
                    >
                      <Plus className="h-3 w-3" />
                      Create a Computed Device
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <>
                {/* 1. Device picker */}
                <div>
                  <div className="flex items-center justify-between mb-0.5">
                    <label className="text-[11px] uppercase tracking-wider text-muted-foreground">
                      Computed Device <span className="text-destructive">*</span>
                      {isEditMode && (
                        <span className="ml-2 normal-case tracking-normal">(locked)</span>
                      )}
                    </label>
                    {!isEditMode && (
                      <button
                        type="button"
                        onClick={() => setDevicesModalOpen(true)}
                        className="text-[10px] text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
                      >
                        <Settings className="h-3 w-3" />
                        Manage…
                      </button>
                    )}
                  </div>
                  {isEditMode ? (
                    <div className="text-xs px-2 py-1.5 bg-card border border-border rounded">
                      {existingCalc?.device_name} (#{existingCalc?.device_id})
                    </div>
                  ) : (
                    <select
                      className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                      value={deviceId === "" ? "" : String(deviceId)}
                      onChange={(e) =>
                        setDeviceId(e.target.value === "" ? "" : Number(e.target.value))
                      }
                    >
                      <option value="">— select Computed Device —</option>
                      {enabledDevices.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name} ({d.computed_tag_count} tag{d.computed_tag_count !== 1 ? "s" : ""})
                        </option>
                      ))}
                    </select>
                  )}
                </div>

                {/* 2. Tag name + data_type */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                      Tag name <span className="text-destructive">*</span>
                      {isEditMode && (
                        <span className="ml-2 normal-case tracking-normal">(locked)</span>
                      )}
                    </label>
                    {isEditMode ? (
                      <div className="text-xs px-2 py-1.5 bg-card border border-border rounded font-mono">
                        {existingCalc?.name}
                      </div>
                    ) : (
                      <input
                        type="text"
                        className="h-7 text-xs bg-card border border-border rounded px-2 w-full font-mono"
                        value={tagName}
                        onChange={(e) => setTagName(e.target.value)}
                        placeholder="e.g. SUM_FlowRates_Hourly"
                      />
                    )}
                  </div>
                  <div>
                    <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                      Data type
                      {isEditMode && (
                        <span className="ml-2 normal-case tracking-normal">(locked)</span>
                      )}
                    </label>
                    {isEditMode ? (
                      <div className="text-xs px-2 py-1.5 bg-card border border-border rounded font-mono">
                        {existingCalc?.data_type}
                      </div>
                    ) : (
                      <select
                        className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                        value={dataType}
                        onChange={(e) => setDataType(e.target.value)}
                      >
                        {DATA_TYPES.map((t) => (
                          <option key={t} value={t}>{t}</option>
                        ))}
                      </select>
                    )}
                  </div>
                </div>

                {/* 3. Description */}
                <div>
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                    Description (optional)
                  </label>
                  <input
                    type="text"
                    className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="What does this calc compute?"
                  />
                </div>

                {/* 4. Block-type picker */}
                <div>
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block">
                    Block type <span className="text-destructive">*</span>
                    {isEditMode && (
                      <span className="ml-2 normal-case tracking-normal">
                        (locked — delete and recreate to change)
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

                {/* 5. Schema-driven config form */}
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

                {/* 6. Rate + enabled */}
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

                {/* 7. Phase 17.0b - Output target */}
                <div className="border border-border rounded p-3 bg-secondary/10">
                  <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
                    Output target
                  </div>
                  <div className="space-y-2">
                    {/* Internal mode */}
                    <label className="flex items-start gap-2 text-xs cursor-pointer">
                      <input
                        type="radio"
                        name="outputMode"
                        checked={outputMode === "internal"}
                        onChange={() => handleOutputModeChange("internal")}
                        className="mt-0.5"
                      />
                      <div className="flex-1">
                        <div className="font-medium">Internal (default)</div>
                        <div className="text-muted-foreground text-[11px] leading-relaxed">
                          The calc's value is written to this computed tag itself.
                          Read it by name like any other tag.
                        </div>
                      </div>
                    </label>

                    {/* External mode */}
                    <label className="flex items-start gap-2 text-xs cursor-pointer">
                      <input
                        type="radio"
                        name="outputMode"
                        checked={outputMode === "external"}
                        onChange={() => handleOutputModeChange("external")}
                        className="mt-0.5"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="font-medium flex items-center gap-1">
                          External
                          <ArrowRight className="h-3 w-3" />
                          <span className="text-muted-foreground font-normal">
                            write to an existing tag
                          </span>
                        </div>
                        <div className="text-muted-foreground text-[11px] leading-relaxed mb-1.5">
                          The calc's value is written to the chosen tag (typically a
                          Modbus tag on another device). The internal tag stays as
                          metadata only and receives no values.
                        </div>
                        {outputMode === "external" && (
                          <>
                            <select
                              className="h-7 text-xs bg-card border border-border rounded px-2 w-full"
                              value={outputTagId === "" ? "" : String(outputTagId)}
                              onChange={(e) =>
                                setOutputTagId(e.target.value === "" ? "" : Number(e.target.value))
                              }
                              disabled={outputCandidates.isLoading}
                            >
                              <option value="">
                                {outputCandidates.isLoading
                                  ? "Loading candidates…"
                                  : `— select target tag (${outputCandidates.data?.length ?? 0} eligible) —`}
                              </option>
                              {(outputCandidates.data ?? []).map((t) => (
                                <option key={t.id} value={t.id}>
                                  {t.device_name} / {t.name} ({t.data_type})
                                </option>
                              ))}
                            </select>
                            <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">
                              Computed-device tags and tags already used as another
                              calc's output are filtered out.
                            </p>
                            {selectedOutputCandidate &&
                             selectedOutputCandidate.data_type !== dataType && (
                              <div className="mt-1.5 text-[10px] text-amber-700 bg-amber-50
                                              border border-amber-200 rounded px-2 py-1
                                              flex items-start gap-1.5">
                                <AlertTriangle className="h-3 w-3 flex-shrink-0 mt-0.5" />
                                <span>
                                  Target's data type is <strong>{selectedOutputCandidate.data_type}</strong>{" "}
                                  but this calc emits <strong>{dataType}</strong>.
                                  Values are written as float64 internally and converted
                                  at read time — usually fine, but verify range and
                                  precision suit the target.
                                </span>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    </label>
                  </div>
                </div>

                {/* Preview */}
                {blockCode && (
                  <details className="text-[10px] text-muted-foreground">
                    <summary className="cursor-pointer hover:text-foreground">
                      Preview request body
                    </summary>
                    <pre className="mt-1 p-2 bg-secondary/30 rounded font-mono text-[10px] overflow-x-auto">
{isEditMode ?
`# PATCH /api/computed-tags/${existingCalc?.id}
${JSON.stringify({
  description: description.trim() || null,
  block_config: blockConfig,
  execution_rate_ms: rateMs,
  enabled,
  output_tag_id: resolveOutputTagId(),
}, null, 2)}` :
`# POST /api/computed-tags
${JSON.stringify({
  device_id: deviceId,
  name: tagName.trim(),
  data_type: dataType,
  description: description.trim() || null,
  block_type: blockCode,
  block_config: blockConfig,
  execution_rate_ms: rateMs,
  enabled,
  output_tag_id: resolveOutputTagId(),
}, null, 2)}`}
                    </pre>
                  </details>
                )}

                {submitError && (
                  <div className="flex items-start gap-2 text-xs text-destructive
                                  bg-destructive/10 border border-destructive/30
                                  rounded p-2">
                    <AlertTriangle className="h-3 w-3 flex-shrink-0 mt-0.5" />
                    <span className="font-mono whitespace-pre-wrap break-words">{submitError}</span>
                  </div>
                )}
              </>
            )}
          </div>

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
              disabled={
                mutation.isPending || !blockCode
                || (!isEditMode && enabledDevices.length === 0)
              }
              className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground
                         hover:bg-primary/90 disabled:opacity-30 inline-flex items-center gap-2"
            >
              {mutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
              {isEditMode ? "Save changes" : "Create computed tag"}
            </button>
          </div>
        </div>
      </div>

      <ComputedDevicesModal
        open={devicesModalOpen}
        onClose={() => setDevicesModalOpen(false)}
      />
    </>
  );
}
