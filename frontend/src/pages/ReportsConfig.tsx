/**
 * ReportsConfig — Phase: report configuration UI.
 *
 * Browser-based configuration for the reporting engine, wired to the
 * /api/report-config endpoints:
 *   - list / create / edit / delete report definitions
 *   - edit a definition's HTML template
 *   - manage its tag set (report_tags)
 *   - link/unlink triggers and destinations
 *   - render on demand (downloads a PDF)
 *
 * Layout: a master list of definitions on the left; an editor panel on the
 * right. Matches the app's iOS design system (PageHeader / SectionCard /
 * Button / Input) and uses the shared api client (bearer token handled there).
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Trash2, Save, FileDown, Clock, FolderOutput, Tags as TagsIcon,
  Loader2, FileText, CheckCircle2, AlertCircle, LayoutGrid, Settings as SettingsIcon, Settings2, Boxes,
  Calendar, History,
} from "lucide-react";
import { api } from "@/lib/api";
import { TOKEN_KEY } from "@/lib/auth";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type Trigger, type TagLite,
  humanizeTrigger, Field, Select,
} from "@/pages/triggers-shared";
// Report-config consolidation — the Triggers and Destinations libraries now
// render as tabs inside this page (previously separate Configure nav pages).
import ReportTriggers from "@/pages/ReportTriggers";
import ReportDestinations from "@/pages/ReportDestinations";
import { BatchControl } from "@/components/reports/BatchControl";
import { ReportPeriodTab } from "@/components/reports/ReportPeriodTab";
import { ReportDataTab } from "@/components/reports/ReportDataTab";
import { ReportRevisionsTab } from "@/components/reports/ReportRevisionsTab";

// ---- types mirroring the backend ----------------------------------------
type Definition = {
  id: number; name: string; description: string | null;
  category: string; report_type: string | null;
  report_code: string | null; area: string | null;
  equipment: string | null; owner_dept: string | null;
  template_html: string | null; template_mode: string | null;
  page_size: string | null; orientation: string | null;
  enabled: boolean;
  status?: string | null; active_revision_id?: number | null;
  trigger_ids?: number[]; destination_ids?: number[];
  destination_fmts?: Record<number, string>;
};
type Destination = { id: number; name: string; dest_type: string; target: string; default_fmts?: string; owner_report_id?: number | null };

const CATEGORIES = ["periodic", "event", "on_demand"];
const PAGE_SIZES = ["A4", "Letter"];
const ORIENTATIONS = ["portrait", "landscape"];

type TopTab = "definitions" | "triggers" | "destinations" | "batch";

export default function ReportsConfig() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  // Top-level tabs: Definitions / Triggers / Destinations. Synced to ?tab=
  // so old /config/report-triggers redirects (and bookmarks) land correctly.
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const [topTab, setTopTab] = useState<TopTab>(
    tabParam === "triggers" || tabParam === "destinations" || tabParam === "batch" ? tabParam : "definitions",
  );
  const switchTab = (t: TopTab) => {
    setTopTab(t);
    setSearchParams(t === "definitions" ? {} : { tab: t }, { replace: true });
  };

  // ---- queries ----
  const defs = useQuery({
    queryKey: ["report-defs"],
    queryFn: () => api.get<Definition[]>("/report-config/definitions"),
  });
  const triggers = useQuery({
    queryKey: ["report-triggers"],
    queryFn: () => api.get<Trigger[]>("/report-config/triggers"),
  });
  const destinations = useQuery({
    queryKey: ["report-dests"],
    queryFn: () => api.get<Destination[]>("/report-config/destinations"),
  });
  const allTags = useQuery({
    queryKey: ["tags-lite"],
    queryFn: () => api.get<TagLite[]>("/tags?limit=1000"),
  });

  // select first definition once loaded
  useEffect(() => {
    if (selectedId == null && defs.data && defs.data.length) {
      setSelectedId(defs.data[0].id);
    }
  }, [defs.data, selectedId]);

  const flash = (kind: "ok" | "err", msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 3500);
  };

  // ---- create definition ----
  const createDef = useMutation({
    mutationFn: () =>
      api.post<Definition>("/report-config/definitions", {
        name: `New Report ${new Date().toISOString().slice(11, 19)}`,
        category: "on_demand", report_type: "current",
        page_size: "A4", orientation: "portrait",
        template_html: "<h1>{{ report.name }}</h1>\n<p>Generated {{ report.generated_at }}</p>",
      }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["report-defs"] });
      setSelectedId(d.id);
      flash("ok", "Report created.");
    },
    onError: (e: any) => flash("err", e?.detail || "Create failed."),
  });

  const selected = useMemo(
    () => defs.data?.find((d) => d.id === selectedId) ?? null,
    [defs.data, selectedId],
  );

  return (
    <div className="p-4">
      <PageHeader
        title="Report Configuration"
        subtitle="Define reports, their template, tags, schedule triggers, and delivery destinations"
        actions={
          topTab === "definitions" ? (
            <Button onClick={() => createDef.mutate()} disabled={createDef.isPending}>
              {createDef.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              <span className="ml-1.5">New report</span>
            </Button>
          ) : null
        }
      />

      {/* top-level segmented control */}
      <div
        className="inline-flex rounded-lg p-0.5 mb-4"
        style={{ backgroundColor: "var(--bg,#f2f2f7)", border: "0.5px solid var(--card-edge,#ddd)" }}
      >
        {([
          { id: "definitions" as const, label: "Definitions", icon: <FileText className="h-3.5 w-3.5" /> },
          { id: "triggers" as const, label: "Triggers", icon: <Clock className="h-3.5 w-3.5" /> },
          { id: "destinations" as const, label: "Destinations", icon: <FolderOutput className="h-3.5 w-3.5" /> },
          { id: "batch" as const, label: "Batch", icon: <Boxes className="h-3.5 w-3.5" /> },
        ]).map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => switchTab(t.id)}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-[13px] font-medium transition-colors"
            style={
              topTab === t.id
                ? { backgroundColor: "var(--bg-elevated,#fff)", color: "var(--ios-blue,#007aff)", boxShadow: "var(--card-shadow)" }
                : { color: "var(--ios-gray-1)" }
            }
          >
            {t.icon}{t.label}
          </button>
        ))}
      </div>

      {topTab === "definitions" && toast && (
        <div
          className="mb-3 flex items-center gap-2 rounded-lg px-3 py-2 text-[13px]"
          style={{
            backgroundColor: toast.kind === "ok" ? "var(--ios-green-soft, #e5f8eb)" : "var(--ios-red-soft, #fdecea)",
            color: toast.kind === "ok" ? "var(--ios-green, #1a7a3e)" : "var(--ios-red, #c0392b)",
          }}
        >
          {toast.kind === "ok" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          {toast.msg}
        </div>
      )}

      {topTab === "definitions" && (
      <div className="grid gap-4" style={{ gridTemplateColumns: "300px 1fr" }}>
        {/* ---- master list ---- */}
        <SectionCard title="Reports" flush>
          {defs.isLoading ? (
            <div className="p-4 text-[13px]" style={{ color: "var(--ios-gray-1)" }}>Loading…</div>
          ) : !defs.data?.length ? (
            <div className="p-4 text-[13px]" style={{ color: "var(--ios-gray-1)" }}>
              No reports yet. Click “New report”.
            </div>
          ) : (
            <ul>
              {defs.data.map((d) => (
                <li key={d.id}>
                  <button
                    onClick={() => setSelectedId(d.id)}
                    className="w-full text-left px-4 py-3 border-b transition-colors"
                    style={{
                      borderColor: "var(--separator, #eee)",
                      backgroundColor: d.id === selectedId ? "var(--ios-blue-soft, #e6f0fe)" : "transparent",
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <FileText className="h-3.5 w-3.5 shrink-0" style={{ color: "var(--ios-blue, #007aff)" }} />
                      <span className="text-[13.5px] font-medium truncate">{d.name}</span>
                    </div>
                    <div className="text-[11px] mt-0.5" style={{ color: "var(--ios-gray-1)" }}>
                      {d.category}{d.report_type ? ` · ${d.report_type}` : ""}{d.enabled ? "" : " · disabled"}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        {/* ---- editor ---- */}
        {selected ? (
          <Editor
            key={selected.id}
            def={selected}
            triggers={triggers.data ?? []}
            destinations={destinations.data ?? []}
            allTags={allTags.data ?? []}
            onSaved={() => { qc.invalidateQueries({ queryKey: ["report-defs"] }); flash("ok", "Saved."); }}
            onDeleted={() => { qc.invalidateQueries({ queryKey: ["report-defs"] }); setSelectedId(null); flash("ok", "Deleted."); }}
            onError={(m) => flash("err", m)}
            onGotoTriggers={() => switchTab("triggers")}
            onGotoDests={() => switchTab("destinations")}
          />
        ) : (
          <SectionCard><div className="p-6 text-[13px]" style={{ color: "var(--ios-gray-1)" }}>Select or create a report.</div></SectionCard>
        )}
      </div>
      )}

      {topTab === "triggers" && <ReportTriggers embedded />}
      {topTab === "destinations" && <ReportDestinations embedded />}
      {topTab === "batch" && <BatchControl />}
    </div>
  );
}

// ===========================================================================
function Editor({
  def, triggers, destinations, allTags, onSaved, onDeleted, onError,
  onGotoTriggers, onGotoDests,
}: {
  def: Definition;
  triggers: Trigger[];
  destinations: Destination[];
  allTags: TagLite[];
  onSaved: () => void;
  onDeleted: () => void;
  onError: (m: string) => void;
  onGotoTriggers: () => void;
  onGotoDests: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<Definition>(def);
  const [rendering, setRendering] = useState(false);
  // multi-select output formats for on-demand download (tick any combination)
  type Fmt = "pdf" | "html" | "json" | "xml";
  const [previewFormats, setPreviewFormats] = useState<Set<Fmt>>(new Set<Fmt>(["pdf"]));
  const toggleFmt = (f: Fmt) =>
    setPreviewFormats((s) => {
      const n = new Set(s);
      n.has(f) ? n.delete(f) : n.add(f);
      return n;
    });

  const set = <K extends keyof Definition>(k: K, v: Definition[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  // ---- save definition fields + template ----
  const save = useMutation({
    mutationFn: () =>
      api.patch(`/report-config/definitions/${def.id}`, {
        name: form.name, description: form.description, category: form.category,
        report_type: form.report_type, template_html: form.template_html,
        template_mode: "html", page_size: form.page_size, orientation: form.orientation,
        report_code: form.report_code, area: form.area,
        equipment: form.equipment, owner_dept: form.owner_dept,
        enabled: form.enabled,
      }),
    onSuccess: onSaved,
    onError: (e: any) => onError(e?.detail || "Save failed."),
  });

  // ---- link/unlink trigger ----
  const linkTrigger = async (tid: number, on: boolean) => {
    try {
      if (on) await api.put(`/report-config/definitions/${def.id}/triggers/${tid}`, {});
      else await api.delete(`/report-config/definitions/${def.id}/triggers/${tid}`);
      qc.invalidateQueries({ queryKey: ["report-defs"] });
    } catch (e: any) { onError(e?.detail || "Trigger link failed."); }
  };
  const linkDest = async (did: number, on: boolean, fmtOverride: string = "") => {
    try {
      if (on) await api.put(`/report-config/definitions/${def.id}/destinations/${did}?fmt=${encodeURIComponent(fmtOverride)}`, {});
      else await api.delete(`/report-config/definitions/${def.id}/destinations/${did}`);
      qc.invalidateQueries({ queryKey: ["report-defs"] });
    } catch (e: any) { onError(e?.detail || "Destination link failed."); }
  };
  // per-report override set for a destination (empty = use the destination default)
  const destOverride = (did: number): string => def.destination_fmts?.[did] ?? "";

  type Tab = "content" | "period" | "data" | "triggers" | "destinations" | "settings" | "revisions";
  const [tab, setTab] = useState<Tab>("content");

  const del = useMutation({
    mutationFn: () => api.delete(`/report-config/definitions/${def.id}`),
    onSuccess: onDeleted,
    onError: (e: any) => onError(e?.detail || "Delete failed."),
  });

  // ---- render (download PDF) — uses saved tags via the render fallback ----
  const render = async () => {
    const fmts = Array.from(previewFormats);
    if (fmts.length === 0) { onError("Select at least one format to download."); return; }
    setRendering(true);
    const base = form.name.replace(/\s+/g, "_");
    const token = window.localStorage.getItem(TOKEN_KEY);
    try {
      // download each selected format in turn
      for (const fmt of fmts) {
        const res = await fetch(`/api/report-config/definitions/${def.id}/render?format=${fmt}`, {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) {
          const t = await res.json().catch(() => ({}));
          throw new Error(t.detail || `HTTP ${res.status} (${fmt})`);
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = `${base}.${fmt}`;
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
      }
    } catch (e: any) {
      onError(e?.message || "Render failed.");
    } finally {
      setRendering(false);
    }
  };

  const linkedTriggers = new Set(def.trigger_ids ?? []);
  const linkedDests = new Set(def.destination_ids ?? []);

  const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
    { id: "content", label: "Content", icon: <LayoutGrid className="h-3.5 w-3.5" /> },
    { id: "period", label: "Period", icon: <Calendar className="h-3.5 w-3.5" /> },
    { id: "data", label: "Data", icon: <TagsIcon className="h-3.5 w-3.5" /> },
    { id: "triggers", label: "Triggers", icon: <Clock className="h-3.5 w-3.5" /> },
    { id: "destinations", label: "Destinations", icon: <FolderOutput className="h-3.5 w-3.5" /> },
    { id: "settings", label: "Settings", icon: <SettingsIcon className="h-3.5 w-3.5" /> },
    { id: "revisions", label: "Revisions", icon: <History className="h-3.5 w-3.5" /> },
  ];

  return (
    <div className="flex flex-col gap-3">
      {/* sticky header: identity + actions, always visible */}
      <div className="sticky top-0 z-10 rounded-xl px-4 py-3 flex items-center justify-between"
        style={{ backgroundColor: "var(--bg-elevated,#fff)", border: "0.5px solid var(--card-edge,#ddd)" }}>
        <div className="min-w-0">
          <input value={form.name} onChange={(e) => set("name", e.target.value)}
            className="text-[15px] font-semibold bg-transparent border-0 p-0 w-full focus:outline-none" />
          <div className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
            {form.category}{form.report_type ? ` · ${form.report_type}` : ""}{form.enabled ? "" : " · disabled"}
          </div>
        </div>
        <div className="flex gap-2 shrink-0">
          <div className="flex items-center gap-1 rounded-lg px-1.5 py-1"
            style={{ border: "0.5px solid var(--card-edge,#ddd)", backgroundColor: "var(--bg,#fff)" }}
            title="Select one or more output formats">
            {(["pdf", "html", "json", "xml"] as Fmt[]).map((f) => {
              const on = previewFormats.has(f);
              return (
                <button key={f} onClick={() => toggleFmt(f)} type="button"
                  className="rounded-md px-2 py-0.5 text-[11px] font-semibold transition-colors"
                  style={{
                    backgroundColor: on ? "var(--ios-blue,#007aff)" : "transparent",
                    color: on ? "#fff" : "var(--ios-gray-1)",
                  }}>
                  {f.toUpperCase()}
                </button>
              );
            })}
          </div>
          <Button variant="outline" size="sm" onClick={render} disabled={rendering || previewFormats.size === 0}>
            {rendering ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileDown className="h-3.5 w-3.5" />}
            <span className="ml-1">Download{previewFormats.size > 1 ? ` ${previewFormats.size} files` : previewFormats.size === 1 ? ` ${Array.from(previewFormats)[0].toUpperCase()}` : ""}</span>
          </Button>
          <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            <span className="ml-1">Save</span>
          </Button>
        </div>
      </div>

      {/* tab bar */}
      <div className="flex gap-1 border-b" style={{ borderColor: "var(--separator,#eee)" }}>
        {TABS.map((tb) => (
          <button key={tb.id} onClick={() => setTab(tb.id)}
            className="flex items-center gap-1.5 px-3.5 py-2 text-[13px] font-medium transition-colors"
            style={{
              borderBottom: tab === tb.id ? "2px solid var(--ios-blue,#007aff)" : "2px solid transparent",
              color: tab === tb.id ? "var(--ios-blue,#007aff)" : "var(--ios-gray-1)",
            }}>
            {tb.icon}{tb.label}
          </button>
        ))}
      </div>

      {/* ---- SETTINGS tab (was "Details") ---- */}
      {tab === "settings" && (
      <SectionCard
        title="Settings"
        action={
          <Button variant="outline" size="sm" onClick={() => del.mutate()} disabled={del.isPending}>
            <Trash2 className="h-3.5 w-3.5" /><span className="ml-1">Delete report</span>
          </Button>
        }
      >
        <div className="grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <Field label="Name">
            <Input value={form.name} onChange={(e) => set("name", e.target.value)} />
          </Field>
          <Field label="Report type">
            <Input value={form.report_type ?? ""} onChange={(e) => set("report_type", e.target.value)} placeholder="hourly / daily / batch …" />
          </Field>
          <Field label="Category">
            <Select value={form.category} options={CATEGORIES} onChange={(v) => set("category", v)} />
          </Field>
          <Field label="Description">
            <Input value={form.description ?? ""} onChange={(e) => set("description", e.target.value)} />
          </Field>
          <Field label="Report code">
            <Input value={form.report_code ?? ""} onChange={(e) => set("report_code", e.target.value)} placeholder="EXP_OIL_HR (unique)" />
          </Field>
          <Field label="Owner dept">
            <Input value={form.owner_dept ?? ""} onChange={(e) => set("owner_dept", e.target.value)} placeholder="Operations / Metering …" />
          </Field>
          <Field label="Area / unit">
            <Input value={form.area ?? ""} onChange={(e) => set("area", e.target.value)} />
          </Field>
          <Field label="Equipment">
            <Input value={form.equipment ?? ""} onChange={(e) => set("equipment", e.target.value)} />
          </Field>
          <Field label="Page size">
            <Select value={form.page_size ?? "A4"} options={PAGE_SIZES} onChange={(v) => set("page_size", v)} />
          </Field>
          <Field label="Orientation">
            <Select value={form.orientation ?? "portrait"} options={ORIENTATIONS} onChange={(v) => set("orientation", v)} />
          </Field>
        </div>
        <label className="mt-3 flex items-center gap-2 text-[13px]">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          Enabled (eligible to fire on triggers)
        </label>
      </SectionCard>
      )}

      {/* ---- CONTENT tab (template) ---- */}
      {tab === "content" && (
      <SectionCard
        title="Template (HTML)"
        subtitle="Jinja2. Use {{ report.name }}, {{ report.generated_at }}, and {% for t in tags_list %}{{ t.name }} {{ t.display }}{% endfor %}"
      >
        <textarea
          value={form.template_html ?? ""}
          onChange={(e) => set("template_html", e.target.value)}
          spellCheck={false}
          className="w-full rounded-lg p-3 font-mono text-[12px]"
          style={{ minHeight: 200, border: "0.5px solid var(--card-edge, #ddd)", backgroundColor: "var(--bg, #fff)" }}
        />
        <p className="mt-1 text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
          Render uses this report’s saved tags (below). Save the template before rendering.
        </p>
      </SectionCard>
      )}

      {/* ---- PERIOD tab (data window) ---- */}
      {tab === "period" && (
        <ReportPeriodTab defId={def.id} onSaved={onSaved} onError={onError} />
      )}

      {/* ---- DATA tab (tag bindings) ---- */}
      {tab === "data" && (
        <ReportDataTab defId={def.id} allTags={allTags} onSaved={onSaved} onError={onError} />
      )}

      {/* ---- REVISIONS tab (config snapshots + activation) ---- */}
      {tab === "revisions" && (
        <ReportRevisionsTab defId={def.id} onSaved={onSaved} onError={onError} />
      )}


      {/* triggers + destinations */}
      {/* ---- TRIGGERS tab (select-only; manage in Report Triggers) ---- */}
      {tab === "triggers" && (
        <SectionCard title={<span className="flex items-center gap-1.5"><Clock className="h-4 w-4" />Triggers</span>}
          subtitle="Select which global triggers fire this report"
          action={
            <button type="button" onClick={onGotoTriggers}
              className="text-[12px] inline-flex items-center gap-1 px-3 py-1.5 rounded-lg"
              style={{ border: "0.5px solid var(--card-edge,#ddd)", color: "var(--ios-blue,#007aff)" }}>
              <Settings2 className="h-3.5 w-3.5" /> Manage triggers
            </button>
          }>
          <ul className="flex flex-col gap-1.5">
            {triggers.map((t) => (
              <li key={t.id}>
                <label className="flex items-start gap-2 text-[13px]">
                  <input type="checkbox" className="mt-0.5" checked={linkedTriggers.has(t.id)}
                    onChange={(e) => linkTrigger(t.id, e.target.checked)} />
                  <span className="min-w-0">
                    <span className="font-medium truncate block">{t.name}</span>
                    <span className="block text-[11px] truncate" style={{ color: "var(--ios-gray-1)" }}>
                      {humanizeTrigger(t, (id) => allTags.find((x) => x.id === id)?.name ?? `tag ${id}`)}
                    </span>
                  </span>
                </label>
              </li>
            ))}
            {triggers.length === 0 && (
              <span className="text-[12px]" style={{ color: "var(--ios-gray-1)" }}>
                No triggers yet. Create them in <button type="button" onClick={onGotoTriggers} className="underline" style={{ color: "var(--ios-blue,#007aff)" }}>Report Triggers</button>.
              </span>
            )}
          </ul>
          <p className="mt-2 text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
            Triggers are managed centrally in Report Triggers and shared across reports. Tick the ones that should fire this report.
          </p>
        </SectionCard>
      )}

      {/* ---- DESTINATIONS tab ---- */}
      {tab === "destinations" && (
        <SectionCard title={<span className="flex items-center gap-1.5"><FolderOutput className="h-4 w-4" />Destinations</span>}
          subtitle="Select destinations; each uses its default formats unless you override"
          action={
            <button type="button" onClick={onGotoDests}
              className="text-[12px] inline-flex items-center gap-1 px-3 py-1.5 rounded-lg"
              style={{ border: "0.5px solid var(--card-edge,#ddd)", color: "var(--ios-blue,#007aff)" }}>
              Manage destinations
            </button>
          }>
          <ul className="flex flex-col gap-2">
            {destinations.map((d) => {
              const linked = linkedDests.has(d.id);
              const dflt = d.default_fmts || "pdf";
              const override = destOverride(d.id);          // "" = no override
              const hasOverride = override.length > 0;
              const activeSet = new Set((hasOverride ? override : dflt).split(",").map((x) => x.trim()).filter(Boolean));
              const toggleOv = (f: string) => {
                const base = new Set((override || dflt).split(",").map((x) => x.trim()).filter(Boolean));
                base.has(f) ? base.delete(f) : base.add(f);
                linkDest(d.id, true, Array.from(base).join(","));   // writing a set = override
              };
              return (
                <li key={d.id} className="rounded-lg p-2" style={{ border: "0.5px solid var(--separator,#eee)" }}>
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-2 text-[13px] flex-1 min-w-0">
                      <input type="checkbox" checked={linked}
                        onChange={(e) => linkDest(d.id, e.target.checked, "")} />
                      <span className="font-medium truncate">{d.name}</span>
                      <span className="shrink-0 text-[11px]" style={{ color: "var(--ios-gray-1)" }}>· {d.dest_type}</span>
                    </label>
                    {linked && hasOverride && (
                      <button onClick={() => linkDest(d.id, true, "")} title="Clear override (use default)"
                        className="shrink-0 text-[11px] underline" style={{ color: "var(--ios-gray-1)" }}>
                        reset to default
                      </button>
                    )}
                  </div>
                  {linked && (
                    <div className="mt-1.5 pl-6">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {(["pdf","html","json","xml","csv"]).map((f) => {
                          const on = activeSet.has(f);
                          return (
                            <button key={f} type="button" onClick={() => toggleOv(f)}
                              className="rounded-md px-2 py-0.5 text-[10.5px] font-semibold transition-colors"
                              style={{
                                backgroundColor: on ? "var(--ios-blue,#007aff)" : "var(--bg,#f2f2f7)",
                                color: on ? "#fff" : "var(--ios-gray-1)",
                              }}>{f.toUpperCase()}</button>
                          );
                        })}
                        <span className="text-[10.5px] ml-1" style={{ color: hasOverride ? "var(--ios-orange,#9a5800)" : "var(--ios-gray-1)" }}>
                          {hasOverride ? "overridden" : `default (${dflt})`}
                        </span>
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
            {destinations.length === 0 && (
              <span className="text-[12px]" style={{ color: "var(--ios-gray-1)" }}>
                No destinations yet. Create them in <button type="button" onClick={onGotoDests} className="underline" style={{ color: "var(--ios-blue,#007aff)" }}>Report Destinations</button>.
              </span>
            )}
          </ul>
        </SectionCard>
      )}


    </div>
  );
}

// ---- small helpers --------------------------------------------------------
