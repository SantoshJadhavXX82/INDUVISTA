/**
 * Report Manager — Phase RM.2 (archive browser).
 *
 * DanPacUI-style two-pane layout:
 *   LEFT  — a drill-down tree: Report -> Year -> Month -> Day -> instances.
 *   RIGHT — an inline preview of the selected file (PDF/HTML render in an
 *           iframe; other formats offer a download).
 *
 * Frontend-only: the tree is built client-side from the SAME endpoint the
 * flat view used (GET /api/report-manager/files), which already returns
 * report_name, generated_at, file_path, fmt and status per record. No
 * backend, migration, or patch-script changes are needed for this version.
 *
 * Actions: Download / Archive / Unarchive / Open-in-new-tab on the selected
 * file. All file fetches are authenticated (bearer header) and streamed to
 * an object URL — never a plain window.open (which can't send the token).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Archive, ArchiveRestore, Activity, CalendarClock, CalendarDays, ChevronDown,
  ChevronRight, Clock, Download, ExternalLink, FileBarChart, FileText, Folder,
  FolderOpen, Inbox, Loader2, RefreshCw, Search, Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import { authHeaders } from "@/lib/authFetch";
import { PageHeader } from "@/components/ui/page-header";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/lib/toast";
import { useTimeFormat } from "@/lib/timeFormat";

// ---- types mirroring the backend response --------------------------------
type FileRow = {
  record_id: number;
  generated_at: string;          // UTC ISO
  file_name: string;
  report_name: string;
  report_type: string | null;
  fmt: string;
  byte_size: number | null;
  triggered_by: string;
  status: "SUCCESS" | "FAILED" | "MISSING";
  file_exists: boolean;
  archived: boolean;
  error: string | null;
};
type FilesResponse = {
  timezone: string;
  range: string;
  total_files: number;
  total_storage_bytes: number;
  truncated: boolean;
  groups: { date: string; rows: FileRow[] }[];
};

type RangeKey = "all" | "30_days" | "7_days" | "today" | "custom";
const RANGE_LABELS: Record<RangeKey, string> = {
  all: "All time",
  "30_days": "Past 30 days",
  "7_days": "Past 7 days",
  today: "Today",
  custom: "Custom",
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// ---- tree shape -----------------------------------------------------------
type DayNode = { key: string; day: number; instances: FileRow[] };
type MonthNode = { key: string; month: number; days: DayNode[]; count: number };
type YearNode = { key: string; year: number; months: MonthNode[]; count: number };
type ReportNode = { key: string; report: string; years: YearNode[]; count: number; lastAt: number };

function buildTree(rows: FileRow[]): ReportNode[] {
  const reports = new Map<string, Map<number, Map<number, Map<number, FileRow[]>>>>();
  for (const r of rows) {
    const d = new Date(r.generated_at);
    const y = d.getFullYear(), m = d.getMonth(), day = d.getDate();
    if (!reports.has(r.report_name)) reports.set(r.report_name, new Map());
    const ry = reports.get(r.report_name)!;
    if (!ry.has(y)) ry.set(y, new Map());
    const ym = ry.get(y)!;
    if (!ym.has(m)) ym.set(m, new Map());
    const md = ym.get(m)!;
    if (!md.has(day)) md.set(day, []);
    md.get(day)!.push(r);
  }

  const out: ReportNode[] = [];
  for (const [report, ry] of reports) {
    const years: YearNode[] = [];
    let rCount = 0, lastAt = 0;
    for (const [year, ym] of [...ry.entries()].sort((a, b) => a[0] - b[0])) {
      const months: MonthNode[] = [];
      let yCount = 0;
      for (const [month, md] of [...ym.entries()].sort((a, b) => a[0] - b[0])) {
        const days: DayNode[] = [];
        let mCount = 0;
        for (const [day, insts] of [...md.entries()].sort((a, b) => a[0] - b[0])) {
          insts.sort((a, b) => +new Date(a.generated_at) - +new Date(b.generated_at));
          for (const i of insts) lastAt = Math.max(lastAt, +new Date(i.generated_at));
          days.push({ key: `${report}|${year}|${month}|${day}`, day, instances: insts });
          mCount += insts.length;
        }
        months.push({ key: `${report}|${year}|${month}`, month, days, count: mCount });
        yCount += mCount;
      }
      years.push({ key: `${report}|${year}`, year, months, count: yCount });
      rCount += yCount;
    }
    out.push({ key: report, report, years, count: rCount, lastAt });
  }
  // Reports alphabetical (stable, predictable like a folder list).
  out.sort((a, b) => a.report.localeCompare(b.report));
  return out;
}

// ---- helpers --------------------------------------------------------------
function bytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${u[i]}`;
}
function statusVariant(s: FileRow["status"]): "success" | "destructive" | "warning" {
  return s === "SUCCESS" ? "success" : s === "FAILED" ? "destructive" : "warning";
}
function isPreviewable(fmt: string): boolean {
  const f = fmt.toLowerCase();
  return f === "pdf" || f === "html" || f === "htm";
}
async function fetchBlob(path: string): Promise<Blob> {
  const res = await fetch(`/api${path}`, { headers: authHeaders() });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); if (j?.detail) detail = j.detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.blob();
}

// ---- tree row primitives --------------------------------------------------
function Twisty({ open }: { open: boolean }) {
  return open
    ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
    : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />;
}

function Branch({
  depth, open, icon, label, count, onClick,
}: {
  depth: number; open: boolean; icon: React.ReactNode; label: React.ReactNode;
  count?: number; onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ paddingLeft: 8 + depth * 14 }}
      className="flex w-full items-center gap-1.5 py-1 pr-2 text-left text-sm hover:bg-muted/60"
    >
      <Twisty open={open} />
      {icon}
      <span className="truncate">{label}</span>
      {count != null && <span className="ml-auto pl-2 text-xs text-muted-foreground">{count}</span>}
    </button>
  );
}

// RD4 — report-type icon + tint (shared language with Diagnostics / Config).
function rmType(name: string, rtype: string | null): { Icon: typeof FileText; soft: string; on: string } {
  const s = `${rtype ?? ""} ${name}`.toLowerCase();
  let Icon: typeof FileText = FileBarChart;
  let tint = ["--ios-blue-soft", "--ios-blue-on-soft"];
  if (/hour/.test(s)) Icon = Clock;
  else if (/dai|week|month|year|annual/.test(s)) Icon = CalendarClock;
  else if (/current|live|status|snapshot|now/.test(s)) { Icon = Activity; tint = ["--ios-teal-soft", "--ios-teal-on-soft"]; }
  else if (/event|alarm|trip/.test(s)) { Icon = Zap; tint = ["--ios-purple-soft", "--ios-purple-on-soft"]; }
  return { Icon, soft: tint[0], on: tint[1] };
}

// ---- page -----------------------------------------------------------------
export default function ReportManager() {
  const qc = useQueryClient();
  const { formatTime } = useTimeFormat();
  const [range, setRange] = useState<RangeKey>("all");
  const [fmtFilter, setFmtFilter] = useState<"all" | "pdf" | "html" | "json" | "xml">("all");
  const [custom, setCustom] = useState({ start: "", end: "" });
  const [includeArchived, setIncludeArchived] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const autoSelectedFor = useRef<string>("");

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  const params = useMemo(() => {
    const p = new URLSearchParams();
    if (range === "all") { p.set("range", "custom"); p.set("start", "2000-01-01"); p.set("limit", "5000"); }
    else if (range === "custom") {
      p.set("range", "custom"); p.set("limit", "5000");
      if (custom.start) p.set("start", custom.start);
      if (custom.end) p.set("end", custom.end);
    } else { p.set("range", range); }
    if (includeArchived) p.set("include_archived", "true");
    if (search) p.set("search", search);
    if (fmtFilter !== "all") p.set("fmt", fmtFilter);
    return p.toString();
  }, [range, custom, includeArchived, search, fmtFilter]);

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["report-manager", params],
    queryFn: () => api.get<FilesResponse>(`/report-manager/files?${params}`),
    placeholderData: (prev) => prev,
  });

  const rows = useMemo(() => (data?.groups ?? []).flatMap((g) => g.rows), [data]);
  const tree = useMemo(() => buildTree(rows), [rows]);
  const byId = useMemo(() => new Map(rows.map((r) => [r.record_id, r])), [rows]);
  const selected = selectedId != null ? byId.get(selectedId) ?? null : null;

  // Auto-expand to + select the newest instance, once per data set.
  useEffect(() => {
    if (!rows.length) return;
    if (autoSelectedFor.current === params) return;
    autoSelectedFor.current = params;
    let newest = rows[0];
    for (const r of rows) if (+new Date(r.generated_at) > +new Date(newest.generated_at)) newest = r;
    const d = new Date(newest.generated_at);
    const rep = newest.report_name, y = d.getFullYear(), m = d.getMonth(), day = d.getDate();
    setExpanded(new Set([rep, `${rep}|${y}`, `${rep}|${y}|${m}`, `${rep}|${y}|${m}|${day}`]));
    setSelectedId(newest.record_id);
  }, [rows, params]);

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  // ---- preview blob (authenticated) --------------------------------------
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    setPreviewUrl(null); setPreviewError(null);
    if (!selected || !selected.file_exists || !isPreviewable(selected.fmt)) return;
    setPreviewLoading(true);
    fetchBlob(`/report-manager/files/${selected.record_id}/preview`)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setPreviewUrl(url);
      })
      .catch((e) => { if (!cancelled) setPreviewError((e as Error).message); })
      .finally(() => { if (!cancelled) setPreviewLoading(false); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [selected?.record_id, selected?.file_exists, selected?.fmt]);

  // ---- actions ------------------------------------------------------------
  const archiveMut = useMutation({
    mutationFn: (row: FileRow) =>
      api.post(`/report-manager/files/${row.record_id}/${row.archived ? "unarchive" : "archive"}`, {}),
    onSuccess: (_d, row) => {
      toast.success(row.archived ? "Report unarchived" : "Report archived");
      qc.invalidateQueries({ queryKey: ["report-manager"] });
    },
    onError: (e) => toast.error(`Archive failed: ${(e as Error).message}`),
  });

  const doDownload = async (row: FileRow) => {
    try {
      const blob = await fetchBlob(`/report-manager/files/${row.record_id}/download`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = row.file_name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (e) { toast.error(`Download failed: ${(e as Error).message}`); }
  };

  const subtitle = data
    ? `${data.total_files} file${data.total_files === 1 ? "" : "s"} · ${bytes(data.total_storage_bytes)} · ${data.timezone}`
    : undefined;

  return (
    <div>
      <PageHeader
        title="Report Manager"
        subtitle={subtitle}
        actions={
          <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching}>
            {isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            <span className="ml-1.5">Refresh</span>
          </Button>
        }
      />

      {/* filter bar */}
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-[var(--radius-lg-2,14px)] border border-border bg-card px-3 py-2.5 shadow-[var(--card-shadow)]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search file or report…"
            className="h-9 w-56 rounded-md border border-border bg-background pl-8 pr-3 text-sm outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <select
          value={range}
          onChange={(e) => setRange(e.target.value as RangeKey)}
          className="h-9 rounded-md border border-border bg-background px-2 text-sm"
        >
          {(Object.keys(RANGE_LABELS) as RangeKey[]).map((k) => (
            <option key={k} value={k}>{RANGE_LABELS[k]}</option>
          ))}
        </select>
        {range === "custom" && (
          <>
            <input type="date" value={custom.start}
              onChange={(e) => setCustom((c) => ({ ...c, start: e.target.value }))}
              className="h-9 rounded-md border border-border bg-background px-2 text-sm" />
            <span className="text-muted-foreground">→</span>
            <input type="date" value={custom.end}
              onChange={(e) => setCustom((c) => ({ ...c, end: e.target.value }))}
              className="h-9 rounded-md border border-border bg-background px-2 text-sm" />
          </>
        )}
        <div className="flex items-center gap-0.5 rounded-[var(--radius-md-2,10px)] p-0.5"
          style={{ border: "0.5px solid var(--card-edge,#ddd)", backgroundColor: "var(--bg,#f2f2f7)" }}
          title="Show only one output format">
          {(["all", "pdf", "html", "json", "xml"] as const).map((f) => {
            const on = fmtFilter === f;
            return (
              <button key={f} type="button" onClick={() => setFmtFilter(f)}
                className="rounded-[7px] px-2.5 py-1 text-[11px] font-semibold uppercase transition-colors"
                style={{ backgroundColor: on ? "var(--ios-blue,#007aff)" : "transparent", color: on ? "#fff" : "var(--ios-gray-1)" }}>
                {f}
              </button>
            );
          })}
        </div>
        <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <input type="checkbox" checked={includeArchived}
            onChange={(e) => setIncludeArchived(e.target.checked)} />
          Show archived
        </label>
      </div>

      {data?.truncated && (
        <div className="mb-3 rounded-md border border-[var(--status-warn-soft)] bg-[var(--status-warn-soft)] px-3 py-2 text-sm text-[var(--status-warn-on-soft)]">
          Showing the most recent 5000 files — narrow the range or search to see older ones.
        </div>
      )}

      {/* two-pane: tree + preview */}
      <div className="flex h-[calc(100vh-230px)] min-h-[460px] overflow-hidden rounded-lg border border-border bg-card">
        {/* LEFT: tree */}
        <div className="w-80 shrink-0 overflow-y-auto border-r border-border">
          {isLoading ? (
            <div className="space-y-2 p-3">
              <Skeleton className="h-6 w-full" /><Skeleton className="h-6 w-5/6" /><Skeleton className="h-6 w-4/6" />
            </div>
          ) : isError ? (
            <div className="p-3 text-sm text-[var(--status-error-on-soft)]">
              Failed to load: {(error as Error).message}
            </div>
          ) : tree.length === 0 ? (
            <div className="flex flex-col items-center gap-2 p-8 text-center text-muted-foreground">
              <Inbox className="h-8 w-8" />
              <p className="text-sm">No reports match these filters.</p>
            </div>
          ) : (
            <div className="py-1">
              {tree.map((rep) => {
                const repOpen = expanded.has(rep.key);
                const rt = rmType(rep.report, null);
                return (
                  <div key={rep.key}>
                    <Branch
                      depth={0} open={repOpen} count={rep.count}
                      icon={<span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md" style={{ backgroundColor: `var(${rt.soft})`, color: `var(${rt.on})` }}><rt.Icon className="h-3.5 w-3.5" /></span>}
                      label={<span className="font-medium">{rep.report}</span>}
                      onClick={() => toggle(rep.key)}
                    />
                    {repOpen && rep.years.map((yr) => {
                      const yOpen = expanded.has(yr.key);
                      return (
                        <div key={yr.key}>
                          <Branch
                            depth={1} open={yOpen} count={yr.count}
                            icon={yOpen ? <FolderOpen className="h-4 w-4 shrink-0" style={{ color: "var(--ios-orange)" }} /> : <Folder className="h-4 w-4 shrink-0" style={{ color: "var(--ios-orange)" }} />}
                            label={yr.year} onClick={() => toggle(yr.key)}
                          />
                          {yOpen && yr.months.map((mo) => {
                            const mOpen = expanded.has(mo.key);
                            return (
                              <div key={mo.key}>
                                <Branch
                                  depth={2} open={mOpen} count={mo.count}
                                  icon={mOpen ? <FolderOpen className="h-4 w-4 shrink-0" style={{ color: "var(--ios-indigo)" }} /> : <Folder className="h-4 w-4 shrink-0" style={{ color: "var(--ios-indigo)" }} />}
                                  label={MONTHS[mo.month]} onClick={() => toggle(mo.key)}
                                />
                                {mOpen && mo.days.map((dn) => {
                                  const dOpen = expanded.has(dn.key);
                                  return (
                                    <div key={dn.key}>
                                      <Branch
                                        depth={3} open={dOpen} count={dn.instances.length}
                                        icon={<CalendarDays className="h-4 w-4 shrink-0" style={{ color: "var(--ios-teal)" }} />}
                                        label={String(dn.day).padStart(2, "0")} onClick={() => toggle(dn.key)}
                                      />
                                      {dOpen && dn.instances.map((inst) => {
                                        const active = inst.record_id === selectedId;
                                        return (
                                          <button
                                            key={inst.record_id}
                                            type="button"
                                            onClick={() => setSelectedId(inst.record_id)}
                                            style={{ paddingLeft: 8 + 4 * 14 }}
                                            className={`flex w-full items-center gap-1.5 py-1 pr-2 text-left text-sm ${active ? "bg-primary/10 text-primary" : "hover:bg-muted/60"} ${inst.archived ? "opacity-60" : ""}`}
                                          >
                                            <FileText className="h-3.5 w-3.5 shrink-0" />
                                            <span className="truncate tabular-nums">{formatTime(inst.generated_at)}</span>
                                            <span className="ml-auto pl-2 text-[10px] uppercase text-muted-foreground">{inst.fmt}</span>
                                          </button>
                                        );
                                      })}
                                    </div>
                                  );
                                })}
                              </div>
                            );
                          })}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* RIGHT: preview */}
        <div className="flex min-w-0 flex-1 flex-col">
          {!selected ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
              <FileText className="h-10 w-10" />
              <p className="text-sm">Select a report on the left to preview it.</p>
            </div>
          ) : (
            <>
              {/* preview header / toolbar */}
              <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
                <div className="flex min-w-0 items-center gap-3">
                  {(() => { const pt = rmType(selected.report_name, selected.report_type); const PIcon = pt.Icon; return (
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-md-2,10px)]"
                      style={{ backgroundColor: `var(${pt.soft})`, color: `var(${pt.on})` }}>
                      <PIcon className="h-[18px] w-[18px]" />
                    </span>
                  ); })()}
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[15px] font-semibold" title={selected.file_name}>{selected.file_name}</span>
                      <Badge variant={statusVariant(selected.status)}>{selected.status}</Badge>
                      {selected.archived && <Badge variant="secondary">archived</Badge>}
                    </div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {selected.report_name}
                      {selected.report_type ? ` · ${selected.report_type}` : ""}
                      {" · "}{bytes(selected.byte_size)}
                      {" · "}{selected.triggered_by}
                    </div>
                  </div>
                </div>
                <div className="ml-auto flex items-center gap-1.5">
                  <Button size="sm" variant="outline" disabled={!selected.file_exists}
                    onClick={() => doDownload(selected)}>
                    <Download className="h-4 w-4" /><span className="ml-1.5">Download</span>
                  </Button>
                  {previewUrl && (
                    <Button size="sm" variant="ghost" onClick={() => window.open(previewUrl, "_blank", "noopener,noreferrer")}>
                      <ExternalLink className="h-4 w-4" /><span className="ml-1.5">New tab</span>
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => archiveMut.mutate(selected)}>
                    {selected.archived ? <ArchiveRestore className="h-4 w-4" /> : <Archive className="h-4 w-4" />}
                    <span className="ml-1.5">{selected.archived ? "Unarchive" : "Archive"}</span>
                  </Button>
                </div>
              </div>

              {/* preview body */}
              <div className="relative flex-1 bg-muted/30">
                {!selected.file_exists ? (
                  <Centered>The file is no longer on disk (record kept for history).</Centered>
                ) : !isPreviewable(selected.fmt) ? (
                  <Centered>{selected.fmt} files can’t be previewed inline — use Download.</Centered>
                ) : previewLoading ? (
                  <div className="flex h-full items-center justify-center text-muted-foreground">
                    <Loader2 className="h-6 w-6 animate-spin" />
                  </div>
                ) : previewError ? (
                  <Centered>Preview failed: {previewError}</Centered>
                ) : previewUrl ? (
                  <iframe title="Report preview" src={previewUrl} className="h-full w-full border-0" />
                ) : null}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}
