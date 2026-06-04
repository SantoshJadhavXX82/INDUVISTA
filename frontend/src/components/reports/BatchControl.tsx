/**
 * Batch control — manual override to start/stop a production batch run. These
 * runs delimit the window for reports whose Period type is "Batch". Today this
 * is the override path (one open batch at a time, DB-enforced); a tag-formula
 * driver can later create the same runs automatically.
 *
 * Backed by:
 *   GET  /report-config/batches/current   (poll)
 *   GET  /report-config/batches?limit=…
 *   POST /report-config/batches/start | /stop
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Play, Square, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Batch = {
  id: number;
  batch_no: string | null;
  started_at: string;
  ended_at: string | null;
  source: string;
  note: string | null;
  created_by: string | null;
  created_at: string;
  status: string; // 'open' | 'closed'
};

function fmt(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
}

function errText(e: unknown): string {
  const a = e as { detail?: string; message?: string };
  return a?.detail ?? a?.message ?? "Request failed.";
}

export function BatchControl() {
  const qc = useQueryClient();
  const [batchNo, setBatchNo] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const current = useQuery({
    queryKey: ["batch-current"],
    queryFn: () => api.get<Batch | null>("/report-config/batches/current"),
    refetchInterval: 5000,
  });
  const recent = useQuery({
    queryKey: ["batches-recent"],
    queryFn: () => api.get<Batch[]>("/report-config/batches?limit=10"),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["batch-current"] });
    qc.invalidateQueries({ queryKey: ["batches-recent"] });
  };

  const start = useMutation({
    mutationFn: () =>
      api.post<Batch>("/report-config/batches/start",
        batchNo.trim() ? { batch_no: batchNo.trim() } : {}),
    onSuccess: () => { setErr(null); setBatchNo(""); refresh(); },
    onError: (e) => setErr(errText(e)),
  });
  const stop = useMutation({
    mutationFn: () => api.post<Batch>("/report-config/batches/stop", {}),
    onSuccess: () => { setErr(null); refresh(); },
    onError: (e) => setErr(errText(e)),
  });

  const open = current.data ?? null;
  const busy = start.isPending || stop.isPending;

  return (
    <div className="space-y-4 max-w-4xl">
      <SectionCard title="Batch control">
        {current.isLoading ? (
          <div className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>Loading…</div>
        ) : (
          <div className="space-y-3">
            {/* status line */}
            <div
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-[13px]"
              style={{
                backgroundColor: open ? "var(--ios-green-soft, #e5f8eb)" : "var(--bg, #f2f2f7)",
                color: open ? "var(--ios-green, #1a7a3e)" : "var(--ios-gray-1)",
              }}
            >
              {open ? (
                <>
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ backgroundColor: "var(--ios-green, #1a7a3e)" }}
                  />
                  Batch <strong>{open.batch_no || `#${open.id}`}</strong> open since {fmt(open.started_at)}
                </>
              ) : (
                <>No batch is currently open.</>
              )}
            </div>

            {/* controls */}
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: "var(--text-secondary)" }}>
                  Batch number (optional)
                </label>
                <Input
                  value={batchNo}
                  onChange={(e) => setBatchNo(e.target.value)}
                  placeholder="e.g. B-2026-0142"
                  disabled={!!open || busy}
                  className="w-56"
                />
              </div>
              <Button onClick={() => start.mutate()} disabled={!!open || busy}>
                {start.isPending ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <Play className="h-4 w-4 mr-1.5" />}
                Start batch
              </Button>
              <Button variant="outline" onClick={() => stop.mutate()} disabled={!open || busy}>
                {stop.isPending ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <Square className="h-4 w-4 mr-1.5" />}
                Stop batch
              </Button>
            </div>

            {err && (
              <p className="text-[13px]" style={{ color: "var(--ios-red, #c0392b)" }}>{err}</p>
            )}

            <p className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
              A report with Period type “Batch / Previous completed” covers the last completed run;
              “Batch / Current” covers the open run up to now. Only one batch can be open at a time.
            </p>
          </div>
        )}
      </SectionCard>

      <SectionCard title="Recent batches">
        {recent.isLoading ? (
          <div className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>Loading…</div>
        ) : !recent.data?.length ? (
          <div className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>No batch runs yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px] border-collapse">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--separator)" }}>
                  <th className="text-left px-3 py-2 font-medium">Batch</th>
                  <th className="text-left px-3 py-2 font-medium">Started</th>
                  <th className="text-left px-3 py-2 font-medium">Ended</th>
                  <th className="text-left px-3 py-2 font-medium">Status</th>
                  <th className="text-left px-3 py-2 font-medium">Source</th>
                  <th className="text-left px-3 py-2 font-medium">By</th>
                </tr>
              </thead>
              <tbody>
                {recent.data.map((b) => (
                  <tr key={b.id} style={{ borderBottom: "0.5px solid var(--separator)" }}>
                    <td className="px-3 py-2">{b.batch_no || `#${b.id}`}</td>
                    <td className="px-3 py-2 tabular-nums">{fmt(b.started_at)}</td>
                    <td className="px-3 py-2 tabular-nums">{fmt(b.ended_at)}</td>
                    <td className="px-3 py-2">
                      <span style={{ color: b.status === "open" ? "var(--ios-green, #1a7a3e)" : "var(--text-secondary)" }}>
                        {b.status}
                      </span>
                    </td>
                    <td className="px-3 py-2">{b.source}</td>
                    <td className="px-3 py-2">{b.created_by ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  );
}
