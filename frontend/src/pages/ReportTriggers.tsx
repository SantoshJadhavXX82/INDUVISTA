/**
 * ReportTriggers — standalone trigger library page.
 *
 * The single place to manage report triggers (create / edit / delete). Triggers
 * created here as "Global" are reusable: any report can select them from its
 * Triggers tab. Reports no longer author triggers inline — they reference the
 * ones managed here.
 *
 * Uses the shared trigger builder modal + humanizer so the per-report Triggers
 * tab and this page stay consistent.
 */
import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Pencil, Trash2, Clock, Zap, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { Button } from "@/components/ui/button";
import {
  type Trigger, type TagLite,
  NewTriggerModal, humanizeTrigger,
} from "@/pages/triggers-shared";

export default function ReportTriggers() {
  const qc = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const [editing, setEditing] = useState<Trigger | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  const triggers = useQuery({
    queryKey: ["report-triggers"],
    queryFn: () => api.get<Trigger[]>("/report-config/triggers"),
  });
  const allTags = useQuery({
    queryKey: ["tags-lite"],
    queryFn: () => api.get<TagLite[]>("/tags?limit=1000"),
  });

  const flash = (kind: "ok" | "err", msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 3500);
  };

  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/report-config/triggers/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["report-triggers"] }); flash("ok", "Trigger deleted."); },
    onError: (e: any) => flash("err", e?.detail || "Delete failed (it may be in use by a report)."),
  });

  const tagName = (id: number) => allTags.data?.find((t) => t.id === id)?.name ?? `tag ${id}`;

  const { globals, customs } = useMemo(() => {
    const all = triggers.data ?? [];
    return {
      globals: all.filter((t) => t.owner_report_id == null),
      customs: all.filter((t) => t.owner_report_id != null),
    };
  }, [triggers.data]);

  const row = (t: Trigger) => (
    <li key={t.id} className="flex items-center gap-2 py-2 border-b" style={{ borderColor: "var(--separator,#eee)" }}>
      <span className="shrink-0 p-1.5 rounded-lg" style={{ backgroundColor: "var(--bg,#f2f2f7)" }}>
        {t.trigger_type === "tag"
          ? <Zap className="h-4 w-4" style={{ color: "var(--ios-orange,#9a5800)" }} />
          : <Clock className="h-4 w-4" style={{ color: "var(--ios-blue,#007aff)" }} />}
      </span>
      <span className="flex-1 min-w-0">
        <span className="flex items-center gap-1.5">
          <span className="text-[13.5px] font-medium truncate">{t.name}</span>
          <span className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold"
            style={{
              backgroundColor: t.owner_report_id != null ? "var(--ios-orange-soft,#fff0e0)" : "var(--ios-blue-soft,#e6f0fe)",
              color: t.owner_report_id != null ? "var(--ios-orange,#9a5800)" : "var(--ios-blue,#0040a0)",
            }}>
            {t.owner_report_id != null ? "Custom" : "Global"}
          </span>
        </span>
        <span className="block text-[11.5px]" style={{ color: "var(--ios-gray-1)" }}>
          {humanizeTrigger(t, tagName)}
        </span>
      </span>
      <button onClick={() => setEditing(t)} title="Edit trigger"
        className="shrink-0 p-1.5 rounded hover:bg-[var(--ios-blue-soft,#e6f0fe)]">
        <Pencil className="h-4 w-4" style={{ color: "var(--ios-blue,#007aff)" }} />
      </button>
      <button onClick={() => { if (confirm(`Delete trigger "${t.name}"?`)) del.mutate(t.id); }} title="Delete trigger"
        className="shrink-0 p-1.5 rounded hover:bg-[var(--ios-red-soft,#fdecea)]">
        <Trash2 className="h-4 w-4" style={{ color: "var(--ios-red,#c0392b)" }} />
      </button>
    </li>
  );

  return (
    <div className="p-4">
      <PageHeader
        title="Report Triggers"
        subtitle="Manage the shared trigger library. Global triggers can be selected by any report."
        actions={
          <Button onClick={() => setShowNew(true)}>
            <Plus className="h-4 w-4" /><span className="ml-1.5">New trigger</span>
          </Button>
        }
      />

      {toast && (
        <div className="mb-3 flex items-center gap-2 rounded-lg px-3 py-2 text-[13px]"
          style={{
            backgroundColor: toast.kind === "ok" ? "var(--ios-green-soft,#e5f8eb)" : "var(--ios-red-soft,#fdecea)",
            color: toast.kind === "ok" ? "var(--ios-green,#1a7a3e)" : "var(--ios-red,#c0392b)",
          }}>
          {toast.kind === "ok" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          {toast.msg}
        </div>
      )}

      <div className="flex flex-col gap-4">
        <SectionCard
          title={<span className="flex items-center gap-1.5" style={{ color: "var(--ios-blue,#0040a0)" }}>Global triggers</span>}
          subtitle="Reusable across all reports — edit once, applies everywhere">
          {triggers.isLoading ? (
            <div className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>Loading…</div>
          ) : globals.length === 0 ? (
            <div className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>
              No global triggers yet. Click “New trigger” (keep “Global” selected).
            </div>
          ) : (
            <ul>{globals.map(row)}</ul>
          )}
        </SectionCard>

        <SectionCard
          title={<span className="flex items-center gap-1.5" style={{ color: "var(--ios-orange,#9a5800)" }}>Custom triggers</span>}
          subtitle="Private to a single report (created from a report's Triggers tab in older versions)">
          {customs.length === 0 ? (
            <div className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>No custom triggers.</div>
          ) : (
            <ul>{customs.map(row)}</ul>
          )}
        </SectionCard>
      </div>

      {showNew && (
        <NewTriggerModal
          reportId={0}
          onClose={() => setShowNew(false)}
          onCreated={() => { setShowNew(false); qc.invalidateQueries({ queryKey: ["report-triggers"] }); flash("ok", "Trigger created."); }}
          onError={(m) => flash("err", m)}
          allTags={allTags.data ?? []}
        />
      )}
      {editing && (
        <NewTriggerModal
          reportId={0}
          editing={editing}
          onClose={() => setEditing(null)}
          onCreated={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["report-triggers"] }); flash("ok", "Trigger updated."); }}
          onError={(m) => flash("err", m)}
          allTags={allTags.data ?? []}
        />
      )}
    </div>
  );
}
