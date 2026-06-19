/**
 * CalcFlowModal — dependency flow diagram for a computed tag.
 *
 * Calls GET /api/calc/definitions/{id}/flow and renders the calc chain
 * left-to-right, recursively: when an input is produced by another
 * calc, that calc's own [inputs -> block -> output] flow is drawn
 * inline in a nested box. Every tag node shows its live value and a
 * quality dot; polls every 2s while open.
 *
 *   ┌────────────────────────────────────────────┐
 *   │ ┌───────────────────────┐                  │
 *   │ │ FT101 ●12.3 → [ADD] → │ Sub1 ●14.8  ┐    │
 *   │ │ const  2.5            │             │    │
 *   │ └───────────────────────┘             ├→[DIV]→ Out ●7.4
 *   │   FT102 ●2.0  ──────────────────────  ┘    │
 *   └────────────────────────────────────────────┘
 */
import { useQuery } from "@tanstack/react-query";
import {
  X, ArrowRight, AlertTriangle, Loader2, RefreshCw, Repeat2, CornerDownRight,
} from "lucide-react";

import { decodeQuality } from "@/lib/calcQuality";
import { BlockIcon } from "@/lib/blockIcons";
import { authHeaders } from "@/lib/authFetch";
import { formatFloat } from "@/lib/format";
import type { CalcDefinition } from "@/types/calcDefinitions";

interface FlowTagNode {
  tag_id: number;
  name: string | null;
  device_name: string | null;
  resolved: boolean;
  value: number | null;
  quality: number | null;
  ts: string | null;
}

interface FlowTagInput extends FlowTagNode {
  kind: "raw" | "computed";
  label?: string;
  calc: FlowCalcNode | null;
}

interface FlowConstInput {
  kind: "const";
  label: string;
  value: number;
}

type FlowInput = FlowTagInput | FlowConstInput;

interface FlowCalcNode {
  def_id: number;
  name: string;
  block_type: string;
  enabled: boolean;
  cycle: boolean;
  truncated?: boolean;
  output: FlowTagNode;
  inputs: FlowInput[];
}

function useCalcFlow(defId: number | null) {
  return useQuery<FlowCalcNode>({
    queryKey: ["calc-flow", defId],
    queryFn: async () => {
      const res = await fetch(`/api/calc/definitions/${defId}/flow`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    enabled: defId != null,
    refetchInterval: 2000,
    staleTime: 1000,
  });
}

function ConstChip({ node }: { node: FlowConstInput }) {
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded border
                     bg-slate-50 text-slate-700 border-slate-300 text-[11px] font-mono"
          title={`${node.label}: constant value`}>
      <span className="text-[9px] uppercase tracking-wider text-muted-foreground">
        {node.label}
      </span>
      {formatFloat(node.value)}
      <span className="text-[9px] text-muted-foreground">const</span>
    </span>
  );
}

function TagChip({ node, output = false }: { node: FlowTagNode; output?: boolean }) {
  const q = decodeQuality(node.quality);
  if (!node.resolved) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-1 rounded border
                       bg-red-50 text-red-800 border-red-300 text-[11px]">
        <AlertTriangle className="h-2.5 w-2.5" />
        tag #{node.tag_id} missing
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded border text-[11px] ${
        output
          ? "bg-violet-50 text-violet-900 border-violet-300"
          : "bg-card border-border"
      }`}
      title={`${node.device_name ?? "?"} / ${node.name ?? node.tag_id}${
        node.quality != null ? ` — st ${node.quality}` : ""
      }${node.ts ? ` — ${node.ts}` : ""}`}
    >
      <span className="flex flex-col leading-tight text-left">
        <span className="font-medium">{node.name ?? `#${node.tag_id}`}</span>
        <span className="text-[9px] text-muted-foreground">{node.device_name ?? ""}</span>
      </span>
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${q.dot}`} aria-hidden />
      <span className="font-mono tabular-nums">
        {node.value != null ? formatFloat(node.value) : "—"}
      </span>
    </span>
  );
}

function FlowNode({ node, depth = 0 }: { node: FlowCalcNode; depth?: number }) {
  if (node.cycle) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-1 rounded border
                       bg-amber-50 text-amber-800 border-amber-300 text-[11px]"
            title="This calc is already shown earlier in the chain (circular dependency)">
        <Repeat2 className="h-3 w-3" />
        {node.name} (cycle)
      </span>
    );
  }
  return (
    <div className={`flex items-center gap-2 flex-wrap ${
      depth > 0 ? "border border-dashed border-border rounded p-2 bg-secondary/10" : ""
    }`}>
      {/* inputs column */}
      <div className="flex flex-col gap-1.5">
        {node.inputs.length === 0 && (
          <span className="text-[10px] text-muted-foreground italic">
            {node.truncated ? "…depth limit…" : "constants only"}
          </span>
        )}
        {node.inputs.map((inp, i) =>
          inp.kind === "const" ? (
            <ConstChip key={i} node={inp} />
          ) : inp.kind === "computed" && inp.calc ? (
            <FlowNode key={i} node={inp.calc} depth={depth + 1} />
          ) : (
            <TagChip key={i} node={inp} />
          ),
        )}
      </div>

      <ArrowRight className="h-4 w-4 text-muted-foreground flex-shrink-0" />

      {/* block chip */}
      <span
        className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded border font-medium text-xs ${
          node.enabled
            ? "bg-indigo-50 text-indigo-900 border-indigo-300"
            : "bg-slate-100 text-slate-500 border-slate-300"
        }`}
        title={`${node.name} (#${node.def_id})${node.enabled ? "" : " — disabled"}`}
      >
        <BlockIcon code={node.block_type} className="h-3.5 w-3.5" />
        {node.block_type}
        {!node.enabled && <span className="text-[9px]">(off)</span>}
      </span>

      <ArrowRight className="h-4 w-4 text-muted-foreground flex-shrink-0" />

      {/* output */}
      <TagChip node={node.output} output />
    </div>
  );
}

interface UsedByConsumer {
  id: number;
  name: string;
  block_type: string;
  enabled: boolean;
  device_name: string | null;
  via_label: string;
}

interface UsedByResponse {
  def_id: number;
  output_tag_id: number;
  consumers: UsedByConsumer[];
}

function useUsedBy(defId: number | null) {
  return useQuery<UsedByResponse>({
    queryKey: ["calc-used-by", defId],
    queryFn: async () => {
      const res = await fetch(`/api/calc/definitions/${defId}/used-by`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    enabled: defId != null,
    staleTime: 10_000,
    gcTime: 30_000,
  });
}

/** Downstream consumers: which calcs use THIS calc's output as an input.
 *  Answers "is it safe to delete/rename this?" at a glance. */
function UsedBySection({ calc }: { calc: CalcDefinition }) {
  const used = useUsedBy(calc.id);
  const consumers = used.data?.consumers ?? [];
  return (
    <div className="mt-4 pt-3 border-t border-border">
      <div className="text-[11px] font-medium text-muted-foreground inline-flex items-center gap-1.5 mb-2">
        <CornerDownRight className="h-3 w-3" />
        Used by (downstream)
      </div>
      {used.isLoading && (
        <div className="text-[11px] text-muted-foreground">Checking…</div>
      )}
      {used.isError && (
        <div className="text-[11px] text-red-700">Could not load downstream usage.</div>
      )}
      {used.data && consumers.length === 0 && (
        <div className="text-[11px] text-emerald-800 bg-emerald-50 border border-emerald-200
                        rounded px-2 py-1.5 inline-block">
          Not used by any other computed tag — safe to delete or rename.
        </div>
      )}
      {consumers.length > 0 && (
        <div className="space-y-1">
          <div className="text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">
            {consumers.length} computed tag{consumers.length > 1 ? "s" : ""} consume this output.
            Deleting this calc leaves {consumers.length > 1 ? "them" : "it"} with a stale input;
            renaming is safe (references are by ID).
          </div>
          <div className="flex flex-wrap gap-1.5 pt-1">
            {consumers.map((c) => (
              <span
                key={c.id}
                className={`inline-flex items-center gap-1.5 px-2 py-1 rounded border text-[11px] ${
                  c.enabled
                    ? "bg-card border-border"
                    : "bg-secondary/40 border-border text-muted-foreground"
                }`}
                title={`${c.name} (#${c.id}) — ${c.block_type} — via ${c.via_label}${
                  c.enabled ? "" : " — disabled"
                }`}
              >
                <BlockIcon code={c.block_type} className="h-3 w-3" />
                {c.device_name ? `${c.device_name} / ` : ""}
                {c.name}
                <span className="text-[9px] text-muted-foreground">via {c.via_label}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


export function CalcFlowModal({
  calc, onClose,
}: {
  calc: CalcDefinition | null;
  onClose: () => void;
}) {
  const flow = useCalcFlow(calc?.id ?? null);

  if (!calc) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
         onClick={onClose}>
      <div className="bg-card border border-border rounded-lg shadow-xl
                      max-w-5xl w-full max-h-[85vh] flex flex-col"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="text-sm font-medium inline-flex items-center gap-2">
            <BlockIcon code={calc.block_type} className="h-4 w-4" />
            Calculation flow — {calc.name}
            <span className="text-[10px] text-muted-foreground font-normal">
              live · refreshes every 2 s · nested boxes are upstream calcs
            </span>
          </div>
          <button type="button" onClick={onClose}
                  className="h-7 w-7 inline-flex items-center justify-center rounded hover:bg-secondary">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 overflow-auto">
          {flow.isLoading && (
            <div className="text-xs text-muted-foreground flex items-center gap-2 py-6 justify-center">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading flow…
            </div>
          )}
          {flow.isError && (
            <div className="text-xs text-red-700 flex items-center gap-2 py-4">
              <AlertTriangle className="h-4 w-4" />
              Failed to load flow: {String(flow.error)}
            </div>
          )}
          {flow.data && <FlowNode node={flow.data} />}

          <UsedBySection calc={calc} />
        </div>

        <div className="px-4 py-2 border-t border-border text-[10px] text-muted-foreground
                        flex items-center gap-2">
          <RefreshCw className="h-2.5 w-2.5" />
          Values update live. Dot = data quality (green good, amber uncertain, red bad).
          Dashed boxes are calcs feeding this one; amber "cycle" marks a circular dependency.
        </div>
      </div>
    </div>
  );
}
