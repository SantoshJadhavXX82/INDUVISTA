/**
 * Report Diagnostics — Phase RM.6 (v5, visual revamp).
 *
 * Same data as v4 (calendar-aligned per-trigger grids, canonical tag health,
 * per-trigger live counters, click-a-block detail) with a clearer, more
 * finished presentation: a fleet-health header, status-accented report cards,
 * and aligned metric columns.
 *
 * Reads GET /api/report-config/diagnostics (no migration).
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity, Calendar, CalendarClock, CalendarDays, CalendarRange,
  ChevronDown, ChevronRight, Clock, Database, FileText, Layers, Loader2,
  RefreshCw, Tag as TagIcon, Timer, X, Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

type Level = "ok" | "warning" | "error";
type SlotStatus = "generated" | "missing" | "failed" | "upcoming";
type Slot = { seq: number; at: string; label: string; status: SlotStatus; detail: string | null };
type Cov = { expected: number; generated: number; missing: number; failed: number; upcoming: number };
type Trig = { trigger_id: number; name: string | null; type: string; cadence: string; span_label: string; kind: string; next_due_at: string | null; coverage: Cov; slots: Slot[] };
type TagH = { tag_id: number; name: string; st: number | null; quality: string; stale: boolean; excluded: boolean; healthy: boolean; age_sec: number | null };
type Report = {
  report_id: number; report_name: string; category: string | null; report_type: string | null; enabled: boolean; level: Level;
  triggers: Trig[]; coverage: Cov;
  timing: { last_ms: number | null; avg_ms: number | null; max_ms: number | null; samples: number };
  timeline: { last_generated_at: string | null; last_success_at: string | null; last_failed_at: string | null; last_error: string | null };
  tags: { total: number; healthy: number; list: TagH[] };
  storage: { files: number; bytes: number; last_file_at: string | null };
};
type Resp = { timezone: string; summary: { reports: number; ok: number; warning: number; error: number; total_missing: number }; reports: Report[] };

const STATUS: Record<Level, { label: string; base: string; soft: string; on: string }> = {
  ok: { label: "Healthy", base: "--status-good", soft: "--status-good-soft", on: "--status-good-on-soft" },
  warning: { label: "Needs attention", base: "--status-warn", soft: "--status-warn-soft", on: "--status-warn-on-soft" },
  error: { label: "Failing", base: "--status-error", soft: "--status-error-soft", on: "--status-error-on-soft" },
};
const SLOT_BG: Record<SlotStatus, string> = {
  generated: "var(--status-good-on-soft)", missing: "var(--status-error-on-soft)",
  failed: "var(--status-warn-on-soft)", upcoming: "transparent",
};

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB", "TB"]; let v = n / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${u[i]}`;
}
function whenShort(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
}
function ms(n: number | null): string { return n == null ? "—" : n < 1000 ? `${n} ms` : `${(n / 1000).toFixed(1)} s`; }
function countdown(iso: string | null, nowMs: number): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime() - nowMs;
  if (d <= 0) return "due now";
  const s = Math.floor(d / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h > 0 ? `${h}h ${m}m ${sec}s` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

/* ---- header: fleet health -------------------------------------------- */
function FleetHealth({ s }: { s: Resp["summary"] }) {
  const total = Math.max(1, s.reports);
  const seg = (n: number, c: string) => n > 0 && (
    <div className="h-full" style={{ width: `${(n / total) * 100}%`, backgroundColor: `var(${c})` }} />
  );
  return (
    <div className="mb-4 rounded-[var(--radius-lg-2)] border border-border bg-card p-4 shadow-[var(--card-shadow)]">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-muted-foreground">Fleet health</div>
          <div className="mt-0.5 text-2xl font-semibold tabular-nums">
            {s.ok}<span className="text-base font-normal text-muted-foreground"> / {s.reports} healthy</span>
          </div>
        </div>
        <div className="flex gap-4 text-sm">
          <Stat n={s.ok} label="Healthy" color="--status-good-on-soft" />
          <Stat n={s.warning} label="Attention" color="--status-warn-on-soft" />
          <Stat n={s.error} label="Failing" color="--status-error-on-soft" />
          <Stat n={s.total_missing} label="Gaps" color="--status-error-on-soft" />
        </div>
      </div>
      <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-muted">
        {seg(s.ok, "--status-good")}{seg(s.warning, "--status-warn")}{seg(s.error, "--status-error")}
      </div>
    </div>
  );
}
function Stat({ n, label, color }: { n: number; label: string; color: string }) {
  return (
    <div className="text-right">
      <div className="text-lg font-semibold tabular-nums" style={{ color: n > 0 ? `var(${color})` : undefined }}>{n}</div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  );
}

/* ---- a labelled metric cell in the row header ------------------------ */
function Metric({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={`min-w-0 ${className}`}>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="truncate text-sm tabular-nums">{children}</div>
    </div>
  );
}

/* ---- per-trigger completeness row ------------------------------------ */
function TriggerRow({ t, nowMs }: { t: Trig; nowMs: number }) {
  const [sel, setSel] = useState<Slot | null>(null);
  const c = t.coverage;
  if (t.kind === "tag") {
    return (
      <div className="rounded-[var(--radius-md-2)] border border-border bg-background p-3">
        <div className="flex items-center gap-2 text-sm">
          <Zap className="h-4 w-4 text-muted-foreground" />
          <span className="font-medium">{t.name || "Tag trigger"}</span>
          <Badge variant="secondary">On tag change</Badge>
          <span className="text-xs text-muted-foreground">fires when its tag condition trips</span>
        </div>
      </div>
    );
  }
  return (
    <div className="rounded-[var(--radius-md-2)] border border-border bg-background p-3">
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="flex items-center gap-1.5 font-medium"><Clock className="h-4 w-4 text-muted-foreground" />{t.cadence}</span>
        <span className="text-xs text-muted-foreground">{t.span_label}</span>
        <span className="ml-auto flex items-center gap-3 text-xs tabular-nums">
          <span className="font-medium">{c.generated}/{c.expected}</span>
          {c.missing > 0 && <span className="text-[var(--status-error-on-soft)]">{c.missing} missing</span>}
          {c.failed > 0 && <span className="text-[var(--status-warn-on-soft)]">{c.failed} failed</span>}
          <span className="flex items-center gap-1 text-muted-foreground"><Timer className="h-3.5 w-3.5" />next {countdown(t.next_due_at, nowMs)}</span>
        </span>
      </div>
      <div className="flex flex-wrap gap-1">
        {t.slots.map((sl) => {
          const upcoming = sl.status === "upcoming";
          const active = sel?.seq === sl.seq;
          return (
            <button key={sl.seq} type="button" onClick={() => setSel(active ? null : sl)}
              title={`${new Date(sl.at).toLocaleString()} · ${sl.status}`}
              className={`flex h-7 min-w-[2.4rem] items-center justify-center rounded-md px-1.5 text-[11px] font-semibold tabular-nums transition-transform hover:scale-110 ${active ? "ring-2 ring-ring ring-offset-1" : ""} ${upcoming ? "border border-dashed border-border text-muted-foreground" : "text-white shadow-sm"}`}
              style={upcoming ? undefined : { backgroundColor: SLOT_BG[sl.status] }}>
              {sl.label}
            </button>
          );
        })}
      </div>
      {sel && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
          <span className="mt-0.5 h-3 w-3 shrink-0 rounded" style={{ backgroundColor: sel.status === "upcoming" ? "var(--border)" : SLOT_BG[sel.status] }} />
          <div className="min-w-0 flex-1">
            <div className="font-medium">{new Date(sel.at).toLocaleString()} · <span className="capitalize">{sel.status}</span></div>
            {sel.detail && <div className="mt-0.5 text-muted-foreground">{sel.detail}</div>}
          </div>
          <button type="button" onClick={() => setSel(null)} className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
        </div>
      )}
    </div>
  );
}

function tagBadge(t: TagH): { variant: "success" | "warning" | "destructive" | "secondary"; text: string } {
  if (t.excluded) return { variant: "secondary", text: "static" };
  if (t.stale) return { variant: "warning", text: "stale" };
  if (t.quality === "good") return { variant: "success", text: "good" };
  if (t.quality === "no_data") return { variant: "secondary", text: "no data" };
  return { variant: "destructive", text: "bad" };
}

export default function ReportDiagnostics() {
  const [openId, setOpenId] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => { const i = setInterval(() => setNowMs(Date.now()), 1000); return () => clearInterval(i); }, []);

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["report-diagnostics"],
    queryFn: () => api.get<Resp>(`/report-config/diagnostics`),
    placeholderData: (p) => p,
    refetchInterval: 60_000,
  });

  const reports = useMemo(() => {
    const order: Record<Level, number> = { error: 0, warning: 1, ok: 2 };
    return [...(data?.reports ?? [])].sort((a, b) =>
      order[a.level] - order[b.level] || b.coverage.missing - a.coverage.missing || a.report_name.localeCompare(b.report_name));
  }, [data]);
  const s = data?.summary;

  return (
    <div>
      <PageHeader
        title="Report Diagnostics"
        subtitle={s ? `${s.reports} reports · ${data?.timezone}` : undefined}
        actions={
          <button type="button" onClick={() => refetch()} disabled={isFetching}
            className="inline-flex h-9 items-center gap-1.5 rounded-[var(--radius-md-2)] border border-border px-3 text-sm hover:bg-accent">
            {isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Refresh
          </button>
        }
      />

      {s && <FleetHealth s={s} />}

      {isLoading ? (
        <div className="space-y-2"><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /></div>
      ) : isError ? (
        <div className="rounded-[var(--radius-md-2)] border border-[var(--status-error-soft)] bg-[var(--status-error-soft)] px-4 py-3 text-sm text-[var(--status-error-on-soft)]">
          Couldn’t load diagnostics: {(error as Error).message}. Check the backend is running, then Refresh.
        </div>
      ) : reports.length === 0 ? (
        <div className="rounded-[var(--radius-lg-2)] border border-dashed border-border py-16 text-center text-muted-foreground">
          No reports yet. Create one in Report Config to see its schedule health here.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <div className="min-w-[920px] space-y-2">
            {reports.map((r) => {
              const open = openId === r.report_id;
              const st = STATUS[r.level];
              const tagsOk = r.tags.total > 0 && r.tags.healthy === r.tags.total;
              return (
                <div key={r.report_id}
                  className="overflow-hidden rounded-[var(--radius-lg-2)] border border-border bg-card shadow-[var(--card-shadow)]"
                  style={{ borderLeft: `3px solid var(${st.base})` }}>
                  {/* header */}
                  <button type="button" onClick={() => setOpenId(open ? null : r.report_id)}
                    className="grid w-full grid-cols-[20px_minmax(220px,1fr)_6rem_5rem_7rem_8rem_9rem] items-center gap-4 px-4 py-3 text-left hover:bg-accent/40">
                    <span className="text-muted-foreground">{open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</span>
                    <span className="flex min-w-0 items-center gap-3">
                      <TypeTile r={r} />
                      <span className="min-w-0">
                        <span className="block truncate text-[11px] uppercase tracking-wide text-muted-foreground">
                          {[r.category, r.report_type].filter(Boolean).join(" · ") || "report"}{!r.enabled && " · disabled"}
                        </span>
                        <span className="block truncate text-[15px] font-semibold leading-tight">{r.report_name}</span>
                        <span className="text-xs text-muted-foreground">{r.triggers.length} trigger{r.triggers.length === 1 ? "" : "s"}</span>
                      </span>
                    </span>

                    <Metric label="Tags">
                      {r.tags.total === 0 ? <span className="text-muted-foreground">—</span>
                        : <span style={{ color: tagsOk ? "var(--status-good-on-soft)" : "var(--status-warn-on-soft)" }}>{r.tags.healthy}/{r.tags.total}</span>}
                    </Metric>
                    <Metric label="Gaps">
                      {r.coverage.missing > 0 ? <span style={{ color: "var(--status-error-on-soft)" }}>{r.coverage.missing}</span>
                        : <span className="text-muted-foreground">0</span>}
                    </Metric>
                    <Metric label="Archive">
                      <span className="flex items-center gap-1 text-muted-foreground"><Database className="h-3.5 w-3.5" />{bytes(r.storage.bytes)}</span>
                    </Metric>
                    <Metric label="Last run">{whenShort(r.timeline.last_generated_at)}</Metric>

                    <span className="justify-self-end">
                      <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                        style={{ backgroundColor: `var(${st.soft})`, color: `var(${st.on})` }}>
                        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: `var(${st.base})` }} />
                        {st.label}
                      </span>
                    </span>
                  </button>

                  {/* expanded */}
                  {open && (
                    <div className="space-y-3 border-t border-border bg-background/40 px-4 py-4">
                      <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
                        <span className="font-medium text-foreground">Completeness by trigger — click a block for details</span>
                        <Legend color={SLOT_BG.generated} label="Generated" />
                        <Legend color={SLOT_BG.missing} label="Missing" />
                        <Legend color={SLOT_BG.failed} label="Failed" />
                        <span className="flex items-center gap-1"><span className="h-3 w-3 rounded border border-dashed border-border" />Upcoming</span>
                      </div>

                      {r.triggers.length === 0
                        ? <div className="text-sm text-muted-foreground">No triggers linked — this report has no schedule. Add one in Report Config.</div>
                        : r.triggers.map((t) => <TriggerRow key={t.trigger_id} t={t} nowMs={nowMs} />)}

                      <div className="grid gap-3 lg:grid-cols-2">
                        <div className="rounded-[var(--radius-md-2)] border border-border p-3 text-sm">
                          <div className="mb-1 flex items-center gap-1.5 font-medium"><Timer className="h-4 w-4" /> Generation timing</div>
                          <div className="text-muted-foreground">last {ms(r.timing.last_ms)} · avg {ms(r.timing.avg_ms)} · max {ms(r.timing.max_ms)} ({r.timing.samples} runs)</div>
                          <div className="mt-1 flex items-center gap-1.5 text-muted-foreground"><Database className="h-4 w-4" />{r.storage.files} files · {bytes(r.storage.bytes)} · last {whenShort(r.storage.last_file_at)}</div>
                          {r.timeline.last_error && (
                            <div className="mt-2 truncate text-xs text-[var(--status-error-on-soft)]" title={r.timeline.last_error}>Last error: {r.timeline.last_error}</div>
                          )}
                        </div>
                        <div className="rounded-[var(--radius-md-2)] border border-border p-3 text-sm">
                          <div className="mb-1 flex items-center gap-1.5 font-medium"><TagIcon className="h-4 w-4" /> Tags ({r.tags.healthy}/{r.tags.total} healthy)</div>
                          {r.tags.total === 0 ? (
                            <div className="text-xs text-muted-foreground">Renders from template/context — no explicit tags bound.</div>
                          ) : (
                            <div className="max-h-44 space-y-1 overflow-y-auto pr-1">
                              {r.tags.list.map((t) => {
                                const b = tagBadge(t);
                                return (
                                  <div key={t.tag_id} className="flex items-center gap-2">
                                    <span className="truncate" title={t.name}>{t.name}</span>
                                    <span className="ml-auto"><Badge variant={b.variant}>{b.text}</Badge></span>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return <span className="flex items-center gap-1"><span className="h-3 w-3 rounded" style={{ backgroundColor: color }} />{label}</span>;
}

/* ---- report-type icon + tint ----------------------------------------- */
type IconType = typeof FileText;
function pickTypeIcon(category: string | null, reportType: string | null, name: string): IconType {
  const s = `${reportType ?? ""} ${name}`.toLowerCase();
  if (/hour/.test(s)) return Clock;
  if (/dai\b|daily/.test(s)) return CalendarDays;
  if (/week/.test(s)) return CalendarRange;
  if (/month/.test(s)) return CalendarRange;
  if (/year|annual/.test(s)) return Calendar;
  if (/current|live|status|snapshot|now/.test(s)) return Activity;
  if (/batch/.test(s)) return Layers;
  if (/event|alarm|trip|trigger/.test(s)) return Zap;
  if (category === "periodic") return CalendarClock;
  if (category === "event") return Zap;
  if (category === "on_demand") return Activity;
  return FileText;
}
function typeTint(category: string | null): { soft: string; on: string } {
  if (category === "periodic") return { soft: "--ios-blue-soft", on: "--ios-blue-on-soft" };
  if (category === "event") return { soft: "--ios-purple-soft", on: "--ios-purple-on-soft" };
  if (category === "on_demand") return { soft: "--ios-teal-soft", on: "--ios-teal-on-soft" };
  return { soft: "--status-neutral-soft", on: "--status-neutral-on-soft" };
}
function TypeTile({ r }: { r: Report }) {
  const Icon = pickTypeIcon(r.category, r.report_type, r.report_name);
  const tint = typeTint(r.category);
  const label = [r.category, r.report_type].filter(Boolean).join(" · ");
  return (
    <span title={label || "report"}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-md-2)]"
      style={{ backgroundColor: `var(${tint.soft})`, color: `var(${tint.on})` }}>
      <Icon className="h-[18px] w-[18px]" />
    </span>
  );
}
