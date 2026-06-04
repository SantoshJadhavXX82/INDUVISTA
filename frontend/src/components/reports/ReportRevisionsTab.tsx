/**
 * Revisions tab — the report's configuration history. Snapshot the current
 * config into a draft, see its validation result, and activate it. Backed by
 * the Phase B3a endpoints:
 *   GET/POST  /report-config/definitions/{id}/revisions
 *   POST      /report-config/definitions/{id}/revisions/{rev}/activate
 *
 * Activation runs validation first (hard failures block; warnings are advisory).
 * Editing the report still takes effect live — making the active revision
 * authoritative for render/scheduler is a later step (B3b).
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, GitBranch, Play, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type RevisionSummary = {
  id: number;
  report_id: number;
  revision_no: number;
  status: string;
  notes: string | null;
  created_by: string | null;
  created_at: string;
  activated_by: string | null;
  activated_at: string | null;
  validation_overall: string | null;
};

type Palette = [string, string];
const STATUS_COLOR: Record<string, Palette> = {
  active: ["var(--ios-green-soft,#e5f8eb)", "var(--ios-green,#1a7a3e)"],
  draft: ["var(--ios-blue-soft,#e6f0fe)", "var(--ios-blue,#0040a0)"],
  superseded: ["var(--ios-gray-5,#eee)", "var(--ios-gray-1,#666)"],
  archived: ["var(--ios-gray-5,#eee)", "var(--ios-gray-1,#666)"],
};
const VALID_COLOR: Record<string, Palette> = {
  passed: ["var(--ios-green-soft,#e5f8eb)", "var(--ios-green,#1a7a3e)"],
  warning: ["var(--ios-amber-soft,#fff3d6)", "var(--ios-amber,#9a6700)"],
  failed: ["var(--ios-red-soft,#fdecea)", "var(--ios-red,#c0392b)"],
};
const GRAY: Palette = ["var(--ios-gray-5,#eee)", "var(--ios-gray-1,#666)"];

function Badge({ text, palette }: { text: string; palette: Palette }) {
  return (
    <span className="rounded-full px-2 py-0.5 text-[11px] font-semibold"
      style={{ backgroundColor: palette[0], color: palette[1] }}>
      {text}
    </span>
  );
}

function ts(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  return isNaN(d.getTime()) ? s : d.toLocaleString();
}

export function ReportRevisionsTab({
  defId, onSaved, onError,
}: {
  defId: number;
  onSaved: () => void;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const revsQ = useQuery({
    queryKey: ["report-revisions", defId],
    queryFn: () => api.get<RevisionSummary[]>(`/report-config/definitions/${defId}/revisions`),
  });
  const [notes, setNotes] = useState("");

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["report-revisions", defId] });
    onSaved();
  };

  const createDraft = useMutation({
    mutationFn: () => api.post(`/report-config/definitions/${defId}/revisions`,
      { notes: notes.trim() || null }),
    onSuccess: () => { setNotes(""); refresh(); },
    onError: (e: any) => onError(e?.detail || "Creating the draft revision failed."),
  });

  const activate = useMutation({
    mutationFn: (revId: number) =>
      api.post(`/report-config/definitions/${defId}/revisions/${revId}/activate`, {}),
    onSuccess: refresh,
    onError: (e: any) => onError(e?.detail || "Activation failed."),
  });

  const revs = revsQ.data ?? [];
  const active = revs.find((r) => r.status === "active");

  return (
    <SectionCard
      title="Revisions"
      subtitle="Immutable snapshots of this report's configuration and their activation history"
      action={
        <Button size="sm" onClick={() => createDraft.mutate()} disabled={createDraft.isPending}>
          {createDraft.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <GitBranch className="h-3.5 w-3.5" />}
          <span className="ml-1">Create draft</span>
        </Button>
      }
    >
      <div className="mb-2 flex items-center gap-1.5 text-[12px]" style={{ color: "var(--ios-gray-1)" }}>
        {active ? (
          <>Active: <Badge text={`revision ${active.revision_no}`} palette={STATUS_COLOR.active} />
            {active.activated_by ? `activated by ${active.activated_by}` : ""}</>
        ) : (
          <>No active revision yet — this report uses its live configuration.</>
        )}
      </div>

      <Input className="mb-2" placeholder="Optional notes for the next draft…"
        value={notes} onChange={(e) => setNotes(e.target.value)} />

      {revsQ.isLoading && (
        <div className="text-[12px]" style={{ color: "var(--ios-gray-1)" }}>Loading…</div>
      )}
      {!revsQ.isLoading && revs.length === 0 && (
        <div className="text-[12px]" style={{ color: "var(--ios-gray-1)" }}>
          No revisions yet. Create a draft to snapshot the current configuration.
        </div>
      )}

      {revs.length > 0 && (
        <div className="rounded-lg overflow-hidden" style={{ border: "0.5px solid var(--card-edge,#ddd)" }}>
          {revs.map((r) => (
            <div key={r.id} className="grid items-center gap-2 px-3 py-2"
              style={{ gridTemplateColumns: "48px 1fr auto", borderTop: "0.5px solid var(--separator,#eee)" }}>
              <div className="text-[13px] font-semibold">#{r.revision_no}</div>
              <div className="flex flex-col gap-1 min-w-0">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <Badge text={r.status} palette={STATUS_COLOR[r.status] ?? GRAY} />
                  {r.validation_overall && (
                    <Badge text={r.validation_overall} palette={VALID_COLOR[r.validation_overall] ?? GRAY} />
                  )}
                  {r.notes && (
                    <span className="text-[12px] truncate" style={{ color: "var(--ios-gray-1)" }} title={r.notes}>
                      {r.notes}
                    </span>
                  )}
                </div>
                <div className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
                  created {ts(r.created_at)} by {r.created_by ?? "—"}
                  {r.activated_at ? ` · activated ${ts(r.activated_at)} by ${r.activated_by ?? "—"}` : ""}
                </div>
              </div>
              <div className="flex items-center justify-end">
                {r.status === "draft" && (
                  <Button variant="outline" size="sm" onClick={() => activate.mutate(r.id)}
                    disabled={activate.isPending}>
                    {activate.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Play className="h-3.5 w-3.5" />}
                    <span className="ml-1">Activate</span>
                  </Button>
                )}
                {r.status === "active" && (
                  <CheckCircle2 className="h-4 w-4" style={{ color: "var(--ios-green,#1a7a3e)" }} />
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 text-[12px]" style={{ color: "var(--ios-gray-1)" }}>
        Activating runs validation first; hard failures block activation. Editing the report
        still takes effect immediately — making the active revision authoritative is a later step.
      </div>
    </SectionCard>
  );
}
