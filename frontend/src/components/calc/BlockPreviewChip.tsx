/**
 * Phase 17.0b - Live preview chip for the calc block configurator.
 *
 * Stateless blocks: JS evaluator runs synchronously on every config
 * change. Zero network. Sample inputs disclosure lets the user override
 * tag values to test specific scenarios.
 *
 * Stateful blocks: renders the full StatefulBlockSimulator inline. The
 * simulator manages its own state-threading loop via the backend
 * /api/computed-tags/preview endpoint.
 */
import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, AlertTriangle, CheckCircle2 } from "lucide-react";
import {
  evaluateBlockJS, buildSamples, blockInputs, isStateful,
  GOOD_QUALITY, GOOD_NON_SPECIFIC,
  type PreviewResult,
} from "@/lib/blockPreview";
import { useLiveValues } from "@/lib/useLiveValues";
import { formatFloat } from "@/lib/format";
import { StatefulBlockSimulator } from "./StatefulBlockSimulator";


type SampleOverride = { value: number | null; quality: number };


/** Map a quality byte to a short human label for read-only display.
 *  Matches the categories used by the backend (GOOD_QUALITY=128). */
function qualityLabel(q: number): string {
  if (q >= 192) return "GOOD";
  if (q >= 128) return "GOOD";
  if (q >= 64)  return "UNCERTAIN";
  return "BAD";
}


interface BlockPreviewChipProps {
  blockCode: string;
  blockConfig: any;
  expectedDataType?: string;
  /** When true, hides the override controls — the chip becomes
   *  display-only, showing live values without letting the user
   *  inject simulated values. Set this when previewing an existing
   *  saved calc (Edit mode): the user is observing, not exploring.
   *  Default false so the create-a-new-calc workflow keeps its
   *  what-if simulator. */
  readOnly?: boolean;
}


export function BlockPreviewChip({
  blockCode, blockConfig, expectedDataType, readOnly = false,
}: BlockPreviewChipProps) {
  if (!blockCode) return null;

  const stateful = isStateful(blockCode);

  return (
    <div className="mt-2 rounded-md border border-border bg-secondary/20 p-2 space-y-1.5">
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {stateful ? "Timeline simulator" : "Preview"}
          {readOnly && (
            <span className="ml-1.5 text-muted-foreground/70 normal-case">
              · read-only (saved calc)
            </span>
          )}
        </span>
      </div>

      {stateful ? (
        <StatefulBlockSimulator blockCode={blockCode} blockConfig={blockConfig} />
      ) : (
        <StatelessPreview
          blockCode={blockCode}
          blockConfig={blockConfig}
          expectedDataType={expectedDataType}
          readOnly={readOnly}
        />
      )}
    </div>
  );
}


function StatelessPreview({
  blockCode, blockConfig, expectedDataType, readOnly = false,
}: BlockPreviewChipProps) {
  const liveValues = useLiveValues();
  const tagIds = useMemo(
    () => blockInputs(blockCode, blockConfig),
    [blockCode, blockConfig],
  );
  const [overrides, setOverrides] = useState<Map<number, SampleOverride>>(new Map());
  const [expanded, setExpanded] = useState(false);

  const result = useMemo<PreviewResult>(() => {
    const samples = buildSamples(blockCode, blockConfig, overrides, liveValues.map);
    return evaluateBlockJS(blockCode, blockConfig, samples);
  }, [blockCode, blockConfig, overrides, liveValues.map]);

  function updateOverride(tagId: number, patch: Partial<SampleOverride>) {
    setOverrides(prev => {
      const next = new Map(prev);
      // Default seeded from the live value (if any) — matches what the
      // preview actually used when no override existed.
      const live = liveValues.map.get(tagId);
      const seedValue = (live && live.value !== null && live.quality >= GOOD_QUALITY)
        ? live.value
        : 1;
      const seedQuality = (live && live.quality >= GOOD_QUALITY)
        ? live.quality
        : GOOD_NON_SPECIFIC;
      const current = next.get(tagId) ?? { value: seedValue, quality: seedQuality };
      next.set(tagId, { ...current, ...patch });
      return next;
    });
  }

  function resetOverrides() {
    setOverrides(new Map());
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <ResultBadge result={result} expectedDataType={expectedDataType} />
      </div>

      {tagIds.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            {expanded ? <ChevronDown className="h-2.5 w-2.5" /> : <ChevronRight className="h-2.5 w-2.5" />}
            Sample inputs ({tagIds.length} tag{tagIds.length !== 1 ? "s" : ""})
            {overrides.size > 0 && (
              <span className="text-amber-700">· {overrides.size} overridden</span>
            )}
          </button>
          {expanded && (
            <div className="mt-1.5 space-y-1">
              {tagIds.map(tid => {
                const ov = overrides.get(tid);
                const live = liveValues.map.get(tid);
                const liveOk = live && live.value !== null && live.quality >= GOOD_QUALITY;
                const v = ov?.value ?? (liveOk ? live.value : 1);
                const q = ov?.quality ?? (liveOk ? live.quality : GOOD_NON_SPECIFIC);
                return (
                  <div key={tid} className="flex items-center gap-2 text-[11px]">
                    <span className="font-mono text-muted-foreground w-16 shrink-0">
                      tag #{tid}
                    </span>
                    {readOnly ? (
                      // Display-only: show the live value as a value-shaped
                      // chip, not an input. No way to override; the operator
                      // is inspecting, not configuring.
                      <span className="h-6 w-24 text-[11px] bg-secondary/40 border border-border
                                       rounded px-1.5 font-mono flex items-center text-muted-foreground">
                        {v === null || v === undefined ? "null" : v}
                      </span>
                    ) : (
                      <input
                        type="number"
                        step="any"
                        className="h-6 w-24 text-[11px] bg-card border border-border rounded px-1.5 font-mono"
                        value={v ?? ""}
                        onChange={(e) => {
                          const raw = e.target.value;
                          updateOverride(tid, { value: raw === "" ? null : Number(raw) });
                        }}
                        placeholder="null"
                      />
                    )}
                    {readOnly ? (
                      <span className="h-6 text-[11px] bg-secondary/40 border border-border
                                       rounded px-1.5 flex items-center text-muted-foreground">
                        {qualityLabel(q)}
                      </span>
                    ) : (
                      <select
                        className="h-6 text-[11px] bg-card border border-border rounded px-1.5"
                        value={q}
                        onChange={(e) => updateOverride(tid, { quality: Number(e.target.value) })}
                      >
                        <option value={GOOD_NON_SPECIFIC}>GOOD (192)</option>
                        <option value={GOOD_QUALITY}>GOOD threshold (128)</option>
                        <option value={64}>UNCERTAIN (64)</option>
                        <option value={0}>BAD (0)</option>
                      </select>
                    )}
                    {!ov && liveOk && (
                      <span className="text-[10px] text-muted-foreground italic">live</span>
                    )}
                  </div>
                );
              })}
              {overrides.size > 0 && !readOnly && (
                <button
                  type="button"
                  onClick={resetOverrides}
                  className="text-[10px] text-muted-foreground hover:text-foreground underline mt-1"
                >
                  Reset to defaults (use live values)
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {result.error && (
        <div className="text-[11px] text-destructive bg-destructive/10
                        border border-destructive/30 rounded px-2 py-1
                        flex items-start gap-1.5">
          <AlertTriangle className="h-3 w-3 flex-shrink-0 mt-0.5" />
          <span className="font-mono break-words">{result.error}</span>
        </div>
      )}
    </div>
  );
}


function ResultBadge({
  result, expectedDataType,
}: {
  result: PreviewResult;
  expectedDataType?: string;
}) {
  if (result.status === "ok") {
    const displayValue = formatPreviewValue(result.value);
    const qualityLabel = qualityToLabel(result.quality);
    const goodEnough = result.quality >= GOOD_QUALITY;
    return (
      <span
        className={`inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded
                    ${goodEnough
                      ? "bg-emerald-50 text-emerald-800 border border-emerald-300"
                      : "bg-amber-50 text-amber-800 border border-amber-300"}`}
        title={[
          `quality: ${qualityLabel} (${result.quality})`,
          expectedDataType ? `target data_type: ${expectedDataType}` : null,
          "computed in browser",
        ].filter(Boolean).join(" · ")}
      >
        {goodEnough ? <CheckCircle2 className="h-2.5 w-2.5" /> : <AlertTriangle className="h-2.5 w-2.5" />}
        = <span className="font-mono font-medium">{displayValue}</span>
        <span className="opacity-70">· {qualityLabel}</span>
      </span>
    );
  }

  const colorByStatus: Record<string, string> = {
    validation_error: "bg-amber-50 text-amber-800 border-amber-300",
    execution_error:  "bg-red-50 text-red-800 border-red-300",
    unknown_block:    "bg-slate-100 text-slate-700 border-slate-300",
    stateful_deferred: "bg-slate-100 text-slate-600 border-slate-300",
  };
  const cls = colorByStatus[result.status] ?? "bg-slate-100 text-slate-600 border-slate-300";
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded border ${cls}`}>
      <AlertTriangle className="h-2.5 w-2.5" />
      {result.status.replace("_", " ")}
    </span>
  );
}


function formatPreviewValue(v: number | null): string {
  if (v === null || v === undefined) return "BAD";
  if (!Number.isFinite(v)) return String(v);
  // Use the central formatter (lib/format.ts) so the preview chip
  // matches every other tag display in the app — no exponential
  // notation, thousands separators on large values, etc.
  return formatFloat(v);
}

function qualityToLabel(q: number): string {
  if (q >= GOOD_NON_SPECIFIC) return "GOOD";
  if (q >= GOOD_QUALITY) return "GOOD(threshold)";
  if (q >= 64) return "UNCERTAIN";
  return "BAD";
}
