/**
 * CalcFlowPreview — suggestion #6 (data-flow preview).
 *
 * Rendered in CreateCalcModal under the Block configuration form.
 * Shows the calc as a left-to-right flow:
 *
 *     [input chips] → [block icon + code] → [output chip]
 *
 * Input chips resolve tag ids to names (useTagsList) and show the live
 * value + quality dot from /api/calc/current-values (polled every 2s,
 * authenticated). Constant operands render as neutral "const" chips.
 * Unknown/deleted tag ids render red. The reference equation for the
 * block is shown underneath.
 *
 * Operand decoding mirrors backend resolve_operand_spec():
 *   int > 0            → tag id
 *   float (int-valued) → tag id; other floats → constant
 *   {tag: id}          → tag id
 *   {value: n}         → constant
 * Operand positions are the known schema keys (left/right/input/...,
 * inputs[]); parameter keys (preset_ms, tolerance, weights, ...) are
 * intentionally not treated as tags.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, AlertTriangle } from "lucide-react";

import { useTagsList } from "@/lib/useTagsList";
import { decodeQuality } from "@/lib/calcQuality";
import { BlockIcon } from "@/lib/blockIcons";
import { blockExample } from "@/lib/blockExamples";
import { authHeaders } from "@/lib/authFetch";
import { formatFloat } from "@/lib/format";

/** Scalar config keys that hold operands (tag-or-constant). */
const OPERAND_SCALAR_KEYS = [
  "left", "right", "input", "condition", "then_value", "else_value",
  "set", "reset", "primary", "standby", "count_up", "count_down",
  "load", "index",
] as const;

/** List config keys whose elements are operands. */
const OPERAND_LIST_KEYS = ["inputs"] as const;

type Operand =
  | { kind: "tag"; tagId: number; label: string }
  | { kind: "const"; value: number; label: string };

function decodeOperand(spec: unknown, label: string): Operand | null {
  if (typeof spec === "boolean" || spec == null) return null;
  if (typeof spec === "number") {
    if (Number.isFinite(spec) && spec > 0 && Number.isInteger(spec)) {
      return { kind: "tag", tagId: spec, label };
    }
    return Number.isFinite(spec) ? { kind: "const", value: spec, label } : null;
  }
  if (typeof spec === "object") {
    const o = spec as Record<string, unknown>;
    if (typeof o.tag === "number" && Number.isInteger(o.tag) && o.tag > 0) {
      return { kind: "tag", tagId: o.tag, label };
    }
    if (typeof o.value === "number" && Number.isFinite(o.value)) {
      return { kind: "const", value: o.value, label };
    }
  }
  return null;
}

function extractOperands(cfg: Record<string, unknown>): Operand[] {
  const out: Operand[] = [];
  for (const key of OPERAND_LIST_KEYS) {
    const arr = cfg[key];
    if (Array.isArray(arr)) {
      arr.forEach((spec, i) => {
        const op = decodeOperand(spec, `${key}[${i}]`);
        if (op) out.push(op);
      });
    }
  }
  for (const key of OPERAND_SCALAR_KEYS) {
    if (key in cfg) {
      const op = decodeOperand(cfg[key], key);
      if (op) out.push(op);
    }
  }
  return out;
}

interface CurrentValueRecord {
  value: number | null;
  quality: number | null;
  ts: string | null;
}

function useLiveValues(enabled: boolean) {
  return useQuery<{ values: Record<string, CurrentValueRecord> }>({
    queryKey: ["calc-current-values"],
    queryFn: async () => {
      const res = await fetch("/api/calc/current-values", {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    enabled,
    refetchInterval: 2000,
    staleTime: 1000,
  });
}

export function CalcFlowPreview({
  blockCode,
  blockConfig,
  outputLabel,
  category,
}: {
  blockCode: string;
  blockConfig: Record<string, unknown>;
  outputLabel: string;
  category?: string | null;
}) {
  const operands = extractOperands(blockConfig ?? {});
  const tags = useTagsList();
  const live = useLiveValues(operands.some((o) => o.kind === "tag"));

  const tagById = useMemo(
    () => new Map((tags.data ?? []).map((t) => [t.id, t])),
    [tags.data],
  );
  const values = live.data?.values ?? {};
  const example = blockExample(blockCode);

  return (
    <div className="border border-border rounded p-3 bg-secondary/10">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
        Data flow (live)
      </div>

      <div className="flex items-center gap-2 flex-wrap text-xs">
        {/* Inputs */}
        <div className="flex flex-col gap-1">
          {operands.length === 0 && (
            <span className="text-[11px] text-muted-foreground italic">
              no operands configured yet
            </span>
          )}
          {operands.map((op, i) => {
            if (op.kind === "const") {
              return (
                <span key={i}
                      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded border
                                 bg-slate-50 text-slate-700 border-slate-300 font-mono text-[11px]"
                      title={`${op.label}: constant`}>
                  <span className="text-[9px] uppercase text-muted-foreground">{op.label}</span>
                  {formatFloat(op.value)}
                </span>
              );
            }
            const meta = tagById.get(op.tagId);
            const lv = values[String(op.tagId)];
            const q = decodeQuality(lv?.quality);
            const missing = tags.isSuccess && !meta;
            return (
              <span key={i}
                    className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-[11px] ${
                      missing
                        ? "bg-red-50 text-red-800 border-red-300"
                        : "bg-card border-border"
                    }`}
                    title={`${op.label}: tag #${op.tagId}${lv?.quality != null ? ` (st ${lv.quality})` : ""}`}>
                <span className="text-[9px] uppercase text-muted-foreground">{op.label}</span>
                {missing ? (
                  <>
                    <AlertTriangle className="h-2.5 w-2.5" />
                    #{op.tagId} missing
                  </>
                ) : (
                  <>
                    <span className="font-medium">{meta?.name ?? `#${op.tagId}`}</span>
                    <span className={`inline-block h-1.5 w-1.5 rounded-full ${q.dot}`} aria-hidden />
                    <span className="font-mono tabular-nums">
                      {lv?.value != null ? formatFloat(lv.value) : "—"}
                    </span>
                  </>
                )}
              </span>
            );
          })}
        </div>

        <ArrowRight className="h-4 w-4 text-muted-foreground flex-shrink-0" />

        {/* Block */}
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded border
                         bg-indigo-50 text-indigo-900 border-indigo-300 font-medium">
          <BlockIcon code={blockCode} category={category} className="h-3.5 w-3.5" />
          {blockCode}
        </span>

        <ArrowRight className="h-4 w-4 text-muted-foreground flex-shrink-0" />

        {/* Output */}
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded border
                         bg-violet-50 text-violet-800 border-violet-300 text-[11px] font-medium">
          {outputLabel}
        </span>
      </div>

      {example && (
        <div className="mt-2 text-[10px] font-mono text-muted-foreground overflow-x-auto whitespace-pre">
          {outputLabel} = {example}
        </div>
      )}
    </div>
  );
}
