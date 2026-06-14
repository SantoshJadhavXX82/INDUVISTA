/**
 * InputStatusPanel — suggestion #3 (input-tag status).
 *
 * Rendered inside a computed tag's expanded row. Calls
 * GET /api/calc/definitions/{id}/inputs (authenticated) and shows each
 * input tag the block actually reads — same extractor the evaluator
 * uses — with live value, quality dot, source device, and sample age.
 * Polls every 2s while expanded.
 *
 * Diagnostic emphasis:
 *   - unresolved inputs (deleted tag) render red with "missing"
 *   - the input(s) dragging the output down (quality = worst_quality
 *     when worst is below GOOD) get a red/amber row highlight
 */
import { useQuery } from "@tanstack/react-query";
import { Loader2, AlertTriangle, CornerDownRight } from "lucide-react";
import { decodeQuality, GOOD_QUALITY } from "@/lib/calcQuality";
import { authHeaders } from "@/lib/authFetch";

interface InputStatusRow {
  order: number;
  tag_id: number;
  tag_name: string | null;
  device_name: string | null;
  data_type: string | null;
  resolved: boolean;
  value: number | null;
  quality: number | null;
  ts: string | null;
}

interface InputStatusResponse {
  definition_id: number;
  block_type: string;
  worst_quality: number | null;
  inputs: InputStatusRow[];
  _error?: string;
  _note?: string;
}

function useInputStatus(defId: number, enabled: boolean) {
  return useQuery<InputStatusResponse>({
    queryKey: ["calc-input-status", defId],
    queryFn: async () => {
      const res = await fetch(`/api/calc/definitions/${defId}/inputs`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    enabled,
    refetchInterval: 2000,
    staleTime: 1000,
    gcTime: 30_000,   // drop cached row data 30s after collapse
  });
}

function age(iso: string | null): string {
  if (!iso) return "never";
  const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 0) return "in future";
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`;
  return `${Math.floor(sec / 86400)}d`;
}

export function InputStatusPanel({ defId }: { defId: number }) {
  const q = useInputStatus(defId, true);

  if (q.isLoading) {
    return (
      <div className="text-[11px] text-muted-foreground flex items-center gap-1.5 mb-3">
        <Loader2 className="h-3 w-3 animate-spin" /> Loading input status…
      </div>
    );
  }
  if (q.isError) {
    return (
      <div className="text-[11px] text-red-700 mb-3">
        Failed to load input status: {String(q.error)}
      </div>
    );
  }
  const data = q.data;
  if (!data) return null;
  if (data._error || data._note) {
    return (
      <div className="text-[11px] text-amber-700 mb-3">
        {data._error ?? data._note}
      </div>
    );
  }
  if (data.inputs.length === 0) {
    return (
      <div className="text-[11px] text-muted-foreground italic mb-3">
        This block reads no input tags (constants only).
      </div>
    );
  }

  const worst = data.worst_quality;
  const outputDegraded = worst != null && worst < GOOD_QUALITY;

  return (
    <div className="mb-3">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1 flex items-center gap-2">
        Input tags
        {outputDegraded && (
          <span className="inline-flex items-center gap-1 normal-case tracking-normal
                           text-[10px] px-1.5 py-0.5 rounded border bg-red-50 text-red-800 border-red-300">
            <AlertTriangle className="h-2.5 w-2.5" />
            output degraded by input quality
          </span>
        )}
      </div>
      <table className="text-xs w-full max-w-2xl">
        <thead>
          <tr className="text-muted-foreground text-[10px] uppercase tracking-wider border-b border-border">
            <th className="text-left  px-2 py-1 font-medium">#</th>
            <th className="text-left  px-2 py-1 font-medium">Input tag</th>
            <th className="text-left  px-2 py-1 font-medium">Device</th>
            <th className="text-right px-2 py-1 font-medium">Value</th>
            <th className="text-left  px-2 py-1 font-medium">Quality</th>
            <th className="text-right px-2 py-1 font-medium">Age</th>
          </tr>
        </thead>
        <tbody>
          {data.inputs.map((inp) => {
            const qm = decodeQuality(inp.quality);
            const isCulprit =
              !inp.resolved ||
              (outputDegraded && (inp.quality ?? 0) === worst);
            const rowCls = !inp.resolved
              ? "bg-red-50"
              : isCulprit
                ? (qm.key === "bad" ? "bg-red-50" : "bg-amber-50")
                : "";
            return (
              <tr key={`${inp.order}-${inp.tag_id}`}
                  className={`border-t border-border ${rowCls}`}>
                <td className="px-2 py-1 text-muted-foreground tabular-nums">
                  <CornerDownRight className="inline h-2.5 w-2.5 mr-0.5 opacity-50" />
                  {inp.order}
                </td>
                <td className="px-2 py-1 font-medium">
                  {inp.resolved ? (
                    <>
                      {inp.tag_name ?? `#${inp.tag_id}`}
                      <span className="ml-1 text-[10px] text-muted-foreground font-mono">
                        #{inp.tag_id}
                      </span>
                    </>
                  ) : (
                    <span className="text-red-700 inline-flex items-center gap-1">
                      <AlertTriangle className="h-2.5 w-2.5" />
                      tag #{inp.tag_id} missing (deleted?)
                    </span>
                  )}
                </td>
                <td className="px-2 py-1 text-[11px] text-muted-foreground">
                  {inp.device_name ?? "—"}
                </td>
                <td className="px-2 py-1 text-right font-mono tabular-nums">
                  {inp.value != null ? inp.value : <span className="text-muted-foreground">—</span>}
                </td>
                <td className="px-2 py-1">
                  <span className="inline-flex items-center gap-1.5"
                        title={qm.raw != null ? `st ${qm.raw}` : "no sample yet"}>
                    <span className={`inline-block h-1.5 w-1.5 rounded-full ${qm.dot}`} aria-hidden />
                    <span className={`text-[11px] ${qm.text}`}>{qm.label}</span>
                  </span>
                </td>
                <td className="px-2 py-1 text-right text-[11px] text-muted-foreground tabular-nums"
                    title={inp.ts ?? undefined}>
                  {age(inp.ts)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
