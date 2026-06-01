/**
 * ReportDestinations — standalone destination library page.
 *
 * The single place to manage report destinations (create / edit / delete).
 * Destinations are Global (reusable by any report) or Custom (private to one
 * report), mirroring Report Triggers. Each destination has a DEFAULT format set
 * (multi-select); a report can override that set per destination from its own
 * Destinations tab.
 */
import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Pencil, Trash2, Folder, HardDrive, Printer, Loader2,
  CheckCircle2, AlertCircle, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const ALL_FMTS = ["pdf", "html", "json", "xml", "csv"] as const;
type Fmt = (typeof ALL_FMTS)[number];

export type Destination = {
  id: number;
  name: string;
  description: string | null;
  dest_type: "folder" | "network_drive" | "printer";
  target: string;
  enabled: boolean;
  owner_report_id: number | null;
  default_fmts: string; // comma-separated
};

function typeIcon(t: string) {
  if (t === "printer") return <Printer className="h-4 w-4" style={{ color: "var(--ios-gray-1)" }} />;
  if (t === "network_drive") return <HardDrive className="h-4 w-4" style={{ color: "var(--ios-gray-1)" }} />;
  return <Folder className="h-4 w-4" style={{ color: "var(--ios-gray-1)" }} />;
}

export default function ReportDestinations() {
  const qc = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const [editing, setEditing] = useState<Destination | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  const dests = useQuery({
    queryKey: ["report-dests"],
    queryFn: () => api.get<Destination[]>("/report-config/destinations"),
  });

  const flash = (kind: "ok" | "err", msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 3500);
  };

  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/report-config/destinations/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["report-dests"] }); flash("ok", "Destination deleted."); },
    onError: (e: any) => flash("err", e?.detail || "Delete failed (it may be in use)."),
  });

  const { globals, customs } = useMemo(() => {
    const all = dests.data ?? [];
    return {
      globals: all.filter((d) => d.owner_report_id == null),
      customs: all.filter((d) => d.owner_report_id != null),
    };
  }, [dests.data]);

  const row = (d: Destination) => (
    <li key={d.id} className="flex items-center gap-2 py-2 border-b" style={{ borderColor: "var(--separator,#eee)" }}>
      <span className="shrink-0 p-1.5 rounded-lg" style={{ backgroundColor: "var(--bg,#f2f2f7)" }}>{typeIcon(d.dest_type)}</span>
      <span className="flex-1 min-w-0">
        <span className="flex items-center gap-1.5">
          <span className="text-[13.5px] font-medium truncate">{d.name}</span>
          <span className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold"
            style={{
              backgroundColor: d.owner_report_id != null ? "var(--ios-orange-soft,#fff0e0)" : "var(--ios-blue-soft,#e6f0fe)",
              color: d.owner_report_id != null ? "var(--ios-orange,#9a5800)" : "var(--ios-blue,#0040a0)",
            }}>
            {d.owner_report_id != null ? "Custom" : "Global"}
          </span>
        </span>
        <span className="block text-[11.5px] truncate" style={{ color: "var(--ios-gray-1)" }}>
          {d.dest_type} · {d.target} · default: {d.default_fmts || "pdf"}
        </span>
      </span>
      <button onClick={() => setEditing(d)} title="Edit destination"
        className="shrink-0 p-1.5 rounded hover:bg-[var(--ios-blue-soft,#e6f0fe)]">
        <Pencil className="h-4 w-4" style={{ color: "var(--ios-blue,#007aff)" }} />
      </button>
      <button onClick={() => { if (confirm(`Delete destination "${d.name}"?`)) del.mutate(d.id); }} title="Delete destination"
        className="shrink-0 p-1.5 rounded hover:bg-[var(--ios-red-soft,#fdecea)]">
        <Trash2 className="h-4 w-4" style={{ color: "var(--ios-red,#c0392b)" }} />
      </button>
    </li>
  );

  return (
    <div className="p-4">
      <PageHeader
        title="Report Destinations"
        subtitle="Manage where reports are delivered. Global destinations can be selected by any report."
        actions={
          <Button onClick={() => setShowNew(true)}>
            <Plus className="h-4 w-4" /><span className="ml-1.5">New destination</span>
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
          title={<span style={{ color: "var(--ios-blue,#0040a0)" }}>Global destinations</span>}
          subtitle="Reusable across all reports">
          {dests.isLoading ? (
            <div className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>Loading…</div>
          ) : globals.length === 0 ? (
            <div className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>No global destinations yet.</div>
          ) : <ul>{globals.map(row)}</ul>}
        </SectionCard>

        <SectionCard
          title={<span style={{ color: "var(--ios-orange,#9a5800)" }}>Custom destinations</span>}
          subtitle="Private to a single report">
          {customs.length === 0 ? (
            <div className="text-[13px]" style={{ color: "var(--ios-gray-1)" }}>No custom destinations.</div>
          ) : <ul>{customs.map(row)}</ul>}
        </SectionCard>
      </div>

      {(showNew || editing) && (
        <DestinationModal
          editing={editing}
          onClose={() => { setShowNew(false); setEditing(null); }}
          onSaved={(msg) => { setShowNew(false); setEditing(null); qc.invalidateQueries({ queryKey: ["report-dests"] }); flash("ok", msg); }}
          onError={(m) => flash("err", m)}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function DestinationModal({
  editing, onClose, onSaved, onError,
}: {
  editing: Destination | null;
  onClose: () => void;
  onSaved: (msg: string) => void;
  onError: (m: string) => void;
}) {
  const isEdit = !!editing;
  const [name, setName] = useState(editing?.name ?? "");
  const [description, setDescription] = useState(editing?.description ?? "");
  const [destType, setDestType] = useState<Destination["dest_type"]>(editing?.dest_type ?? "folder");
  const [target, setTarget] = useState(editing?.target ?? "");
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);
  const [fmts, setFmts] = useState<Set<Fmt>>(
    new Set((editing?.default_fmts || "pdf").split(",").map((x) => x.trim()).filter(Boolean) as Fmt[]));
  const [saving, setSaving] = useState(false);

  const toggle = (f: Fmt) => setFmts((s) => { const n = new Set(s); n.has(f) ? n.delete(f) : n.add(f); return n; });

  const submit = async () => {
    if (!name.trim()) { onError("Name is required."); return; }
    if (!target.trim()) { onError("Target (path / printer) is required."); return; }
    if (fmts.size === 0) { onError("Pick at least one default format."); return; }
    setSaving(true);
    const body = {
      name: name.trim(), description: description.trim() || null,
      dest_type: destType, target: target.trim(), enabled,
      default_fmts: ALL_FMTS.filter((f) => fmts.has(f)).join(","),
    };
    try {
      if (isEdit && editing) await api.patch(`/report-config/destinations/${editing.id}`, body);
      else await api.post("/report-config/destinations", body);
      onSaved(isEdit ? "Destination updated." : "Destination created.");
    } catch (e: any) {
      onError(e?.detail || (isEdit ? "Update failed." : "Create failed."));
    } finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.35)" }} onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl p-5"
        style={{ backgroundColor: "var(--bg-elevated,#fff)", boxShadow: "var(--card-shadow)" }}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-[16px] font-semibold">{isEdit ? "Edit destination" : "New destination"}</h2>
          <button onClick={onClose}><X className="h-4 w-4" /></button>
        </div>

        <div className="flex flex-col gap-3">
          <label className="text-[12px] font-medium">Name
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Local Archive" />
          </label>
          <label className="text-[12px] font-medium">Type
            <select value={destType} onChange={(e) => setDestType(e.target.value as any)}
              className="w-full rounded-lg px-2 h-9 text-[13px] mt-1"
              style={{ border: "0.5px solid var(--card-edge,#ddd)", backgroundColor: "var(--bg,#fff)" }}>
              <option value="folder">Folder</option>
              <option value="network_drive">Network drive</option>
              <option value="printer">Printer</option>
            </select>
          </label>
          <label className="text-[12px] font-medium">Target (path / UNC / printer)
            <Input value={target} onChange={(e) => setTarget(e.target.value)}
              placeholder={destType === "printer" ? "\\\\printserver\\HP-LJ" : "C:\\reports\\archive"} />
          </label>
          <label className="text-[12px] font-medium">Description (optional)
            <Input value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>

          <div>
            <span className="text-[12px] font-medium">Default formats (global)</span>
            <div className="flex gap-1 mt-1">
              {ALL_FMTS.map((f) => {
                const on = fmts.has(f);
                return (
                  <button key={f} type="button" onClick={() => toggle(f)}
                    className="flex-1 rounded-md py-1.5 text-[11px] font-semibold transition-colors"
                    style={{
                      backgroundColor: on ? "var(--ios-blue,#007aff)" : "var(--bg,#f2f2f7)",
                      color: on ? "#fff" : "var(--text-primary,#1c2530)",
                    }}>{f.toUpperCase()}</button>
                );
              })}
            </div>
            <p className="mt-1 text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
              These formats are delivered by default. A report can override this set in its own Destinations tab.
            </p>
          </div>

          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled
          </label>
        </div>

        <div className="flex justify-end gap-2 mt-4">
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={submit} disabled={saving}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
            <span className="ml-1">{isEdit ? "Save changes" : "Create"}</span>
          </Button>
        </div>
      </div>
    </div>
  );
}
