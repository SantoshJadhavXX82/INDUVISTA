/**
 * Reports — Phase 20. Shift-summary report: pick a date and a set of tags
 * (via group picker and/or manual add), then view per-tag aggregates with
 * shifts as columns and tags as rows. Reads GET /api/reports/shift-summary.
 *
 * This is the on-screen view; PDF export builds on the same data next.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, X, CalendarDays, FileBarChart, Loader2, Download } from "lucide-react";
import { api } from "@/lib/api";
import { TOKEN_KEY } from "@/lib/auth";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";

// ---- types mirroring the backend response -------------------------------
type TagShiftStat = {
  tag_id: number;
  name: string;
  engineering_unit: string | null;
  data_type: string;
  avg: number | null;
  min: number | null;
  max: number | null;
  first: number | null;
  last: number | null;
  first_text: string | null;
  last_text: string | null;
  sample_count: number;
  good_count: number;
  bad_count: number;
};
type ShiftWindow = {
  code: string;
  label: string;
  start_local: string;
  end_local: string;
  start_utc: string;
  end_utc: string;
  tags: TagShiftStat[];
};
type ShiftSummary = {
  report_date: string;
  timezone: string;
  generated_at: string;
  tag_ids: number[];
  shifts: ShiftWindow[];
};

type LiveTag = { tag_id: number; tag_name: string; group_ids: number[] };
type Group = { id: number; name: string };

function num(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  // Compact but precise: up to 4 sig digits past the decimal, trimmed.
  const s = Math.abs(v) >= 1000 ? v.toFixed(1) : v.toPrecision(5);
  return parseFloat(s).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function todayLocalISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function Reports() {
  const [date, setDate] = useState<string>(todayLocalISO());
  const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);
  const [tagToAdd, setTagToAdd] = useState<string>("");
  const [groupToAdd, setGroupToAdd] = useState<string>("");
  const [runKey, setRunKey] = useState<{ date: string; ids: number[] } | null>(null);

  // All tags (for the manual picker + group expansion). /api/live carries
  // tag_id, tag_name, and group_ids — everything we need, no extra endpoint.
  const live = useQuery({
    queryKey: ["live"],
    queryFn: () => api.get<LiveTag[]>("/live"),
  });
  const groups = useQuery({
    queryKey: ["groups"],
    queryFn: () => api.get<Group[]>("/groups"),
  });

  const allTags = live.data ?? [];
  const tagById = useMemo(() => {
    const m = new Map<number, LiveTag>();
    for (const t of allTags) m.set(t.tag_id, t);
    return m;
  }, [allTags]);

  function addTag(id: number) {
    setSelectedTagIds((prev) => (prev.includes(id) ? prev : [...prev, id]));
  }
  function removeTag(id: number) {
    setSelectedTagIds((prev) => prev.filter((x) => x !== id));
  }
  function addGroup(groupId: number) {
    const ids = allTags.filter((t) => t.group_ids?.includes(groupId)).map((t) => t.tag_id);
    setSelectedTagIds((prev) => Array.from(new Set([...prev, ...ids])));
  }

  // The report query — only runs when the user clicks "Run report" (runKey set).
  const report = useQuery({
    queryKey: ["shift-summary", runKey?.date, runKey?.ids],
    enabled: !!runKey && (runKey?.ids.length ?? 0) > 0,
    queryFn: () => {
      const params = new URLSearchParams({ date: runKey!.date });
      for (const id of runKey!.ids) params.append("tag_ids", String(id));
      return api.get<ShiftSummary>(`/reports/shift-summary?${params.toString()}`);
    },
  });

  const [pdfBusy, setPdfBusy] = useState(false);

  async function downloadPdf() {
    if (!runKey || runKey.ids.length === 0) return;
    setPdfBusy(true);
    try {
      const params = new URLSearchParams({ date: runKey.date });
      for (const id of runKey.ids) params.append("tag_ids", String(id));
      const token = window.localStorage.getItem(TOKEN_KEY);
      const res = await fetch(`/api/reports/shift-summary.pdf?${params.toString()}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!res.ok) throw new Error(`PDF request failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `shift_summary_${runKey.date}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error("PDF download failed", e);
    } finally {
      setPdfBusy(false);
    }
  }

  function runReport() {
    if (selectedTagIds.length === 0) return;
    setRunKey({ date, ids: [...selectedTagIds] });
  }

  const shifts = report.data?.shifts ?? [];

  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <PageHeader
        title="Reports"
        subtitle="Shift summary — per-tag aggregates for each shift on a chosen day"
      />

      {/* ---- Builder ---- */}
      <SectionCard>
        <div className="flex flex-wrap items-end gap-4">
          {/* Date */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted-foreground inline-flex items-center gap-1">
              <CalendarDays className="h-3.5 w-3.5" /> Report date
            </label>
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="h-9 px-2.5 rounded-md border text-sm"
              style={{ borderColor: "var(--separator)", backgroundColor: "var(--surface)" }}
            />
          </div>

          {/* Group picker */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted-foreground">Add a group's tags</label>
            <select
              value={groupToAdd}
              onChange={(e) => {
                const gid = Number(e.target.value);
                if (gid) addGroup(gid);
                setGroupToAdd("");
              }}
              className="h-9 px-2.5 rounded-md border text-sm min-w-[180px]"
              style={{ borderColor: "var(--separator)", backgroundColor: "var(--surface)" }}
            >
              <option value="">Choose a group…</option>
              {(groups.data ?? []).map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </select>
          </div>

          {/* Manual tag add */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted-foreground">Add a tag</label>
            <select
              value={tagToAdd}
              onChange={(e) => {
                const tid = Number(e.target.value);
                if (tid) addTag(tid);
                setTagToAdd("");
              }}
              className="h-9 px-2.5 rounded-md border text-sm min-w-[220px]"
              style={{ borderColor: "var(--separator)", backgroundColor: "var(--surface)" }}
            >
              <option value="">Choose a tag…</option>
              {allTags
                .filter((t) => !selectedTagIds.includes(t.tag_id))
                .map((t) => (
                  <option key={t.tag_id} value={t.tag_id}>{t.tag_name}</option>
                ))}
            </select>
          </div>

          <div className="flex-1" />

          <Button onClick={runReport} disabled={selectedTagIds.length === 0 || report.isFetching}>
            {report.isFetching ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <FileBarChart className="h-4 w-4 mr-1.5" />}
            Run report
          </Button>
          <Button
            variant="outline"
            onClick={downloadPdf}
            disabled={!report.data || pdfBusy}
            title={!report.data ? "Run a report first" : "Download this report as PDF"}
          >
            {pdfBusy ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <Download className="h-4 w-4 mr-1.5" />}
            Download PDF
          </Button>
        </div>

        {/* Selected tags chips */}
        {selectedTagIds.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-3">
            {selectedTagIds.map((id) => (
              <span
                key={id}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
                style={{ backgroundColor: "var(--ios-blue-soft)", color: "var(--ios-blue-on-soft)" }}
              >
                {tagById.get(id)?.tag_name ?? `Tag ${id}`}
                <button onClick={() => removeTag(id)} className="hover:opacity-70" title="Remove">
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            <button
              onClick={() => setSelectedTagIds([])}
              className="text-xs text-muted-foreground hover:text-foreground ml-1"
            >
              Clear all
            </button>
          </div>
        )}
      </SectionCard>

      {/* ---- Results ---- */}
      {report.isError && (
        <SectionCard>
          <p className="text-sm" style={{ color: "var(--ios-red)" }}>
            Couldn't load the report. Check the selected tags and try again.
          </p>
        </SectionCard>
      )}

      {report.data && shifts.length > 0 && (
        <SectionCard>
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                Shift summary — {report.data.report_date}
              </h3>
              <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                {report.data.timezone} · generated {new Date(report.data.generated_at).toLocaleString()}
              </p>
            </div>
            <StatusPill variant="info" size="sm">{report.data.tag_ids.length} tags · {shifts.length} shifts</StatusPill>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--separator)" }}>
                  <th className="text-left px-3 py-2 font-medium sticky left-0" style={{ backgroundColor: "var(--surface)" }}>Tag</th>
                  {shifts.map((s) => (
                    <th key={s.code} className="text-right px-3 py-2 font-medium">
                      <div>{s.code} · {s.label}</div>
                      <div className="text-[10px] font-normal" style={{ color: "var(--text-secondary)" }}>
                        {new Date(s.start_local).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–
                        {new Date(s.end_local).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.data.tag_ids.map((tid) => {
                  // Find this tag's stat in each shift (same tag id across columns).
                  const perShift = shifts.map((s) => s.tags.find((t) => t.tag_id === tid));
                  const meta = perShift.find(Boolean);
                  return (
                    <tr key={tid} style={{ borderBottom: "0.5px solid var(--separator)" }}>
                      <td className="px-3 py-2 align-top sticky left-0" style={{ backgroundColor: "var(--surface)" }}>
                        <div className="font-medium" style={{ color: "var(--text-primary)" }}>{meta?.name ?? `Tag ${tid}`}</div>
                        {meta?.engineering_unit && (
                          <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>{meta.engineering_unit}</div>
                        )}
                      </td>
                      {perShift.map((st, i) => (
                        <td key={i} className="px-3 py-2 text-right align-top">
                          {!st || st.sample_count === 0 ? (
                            <span style={{ color: "var(--text-secondary)" }}>—</span>
                          ) : st.avg !== null ? (
                            <div>
                              <div className="font-medium tabular-nums" style={{ color: "var(--text-primary)" }}>
                                {num(st.avg)}
                              </div>
                              <div className="text-[10px] tabular-nums" style={{ color: "var(--text-secondary)" }}>
                                min {num(st.min)} · max {num(st.max)}
                              </div>
                              <div className="text-[10px] tabular-nums" style={{ color: "var(--text-secondary)" }}>
                                first {num(st.first)} · last {num(st.last)}
                              </div>
                              {st.bad_count > 0 && (
                                <div className="text-[10px]" style={{ color: "var(--ios-orange)" }}>
                                  {st.bad_count} bad / {st.sample_count}
                                </div>
                              )}
                            </div>
                          ) : (
                            <div className="text-xs" style={{ color: "var(--text-primary)" }}>
                              {st.last_text ?? st.first_text ?? "—"}
                            </div>
                          )}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="text-[10px] mt-3" style={{ color: "var(--text-secondary)" }}>
            Each cell shows the shift average (large), with min/max and first/last below. Aggregates use GOOD-quality
            samples only; a "bad" count appears when some samples in the window were not GOOD. Empty cells (—) mean no
            samples were logged in that shift window (e.g. a future shift, or a tag on on-change logging that didn't change).
          </p>
        </SectionCard>
      )}

      {report.data && shifts.length === 0 && (
        <SectionCard>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>No shift data for this date.</p>
        </SectionCard>
      )}

      {!report.data && !report.isFetching && (
        <SectionCard>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Pick a date, add tags (individually or by group), then <strong>Run report</strong> to see the shift summary.
          </p>
        </SectionCard>
      )}
    </div>
  );
}
