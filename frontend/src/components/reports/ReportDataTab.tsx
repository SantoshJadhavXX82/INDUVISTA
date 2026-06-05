/**
 * Data tab — the report's binding grid. Each bound tag gets a data function
 * (how it's aggregated over the period window), a quality rule, optional
 * display name and decimals. Backed by GET/PUT
 * /report-config/definitions/{id}/bindings.
 *
 * The data function only changes the output when the report has a Period rule;
 * with no period the report is a live snapshot and every function reads as the
 * latest value. Quality is derived from the project `st` standard (st >= 128 =
 * good) in the backend aggregation layer.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Save, X, ListTree } from "lucide-react";
import { api } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, type TagLite } from "@/pages/triggers-shared";
import { TagTreePicker } from "@/components/reports/TagTreePicker";

type Binding = {
  tag_id: number;
  tag_name: string | null;
  position: number;
  display_name: string | null;
  data_function: string;
  quality_rule: string;
  decimal_places: number | null;
};

type Row = {
  tag_id: number;
  tag_name: string;
  display_name: string;
  data_function: string;
  quality_rule: string;
  decimals: string; // text; "" => null
};

const FUNCS = ["latest", "first", "last", "average", "min", "max", "sum", "count", "delta", "availability", "missing_pct"];
const FUNC_LABELS = ["Latest", "First", "Last", "Average", "Min", "Max", "Total (sum)", "Count", "Delta (last−first)", "Availability %", "Missing %"];
const QUALITY = ["all", "good_only", "good_uncertain"];
const QUALITY_LABELS = ["Include all", "Good only", "Good + uncertain"];

export function ReportDataTab({
  defId, allTags, onSaved, onError,
}: {
  defId: number;
  allTags: TagLite[];
  onSaved: () => void;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const bindingsQ = useQuery({
    queryKey: ["report-bindings", defId],
    queryFn: () => api.get<Binding[]>(`/report-config/definitions/${defId}/bindings`),
  });

  const [rows, setRows] = useState<Row[]>([]);
  useEffect(() => {
    if (bindingsQ.data) {
      setRows(bindingsQ.data.map((b) => ({
        tag_id: b.tag_id,
        tag_name: b.tag_name ?? `tag ${b.tag_id}`,
        display_name: b.display_name ?? "",
        data_function: b.data_function ?? "latest",
        quality_rule: b.quality_rule ?? "all",
        decimals: b.decimal_places == null ? "" : String(b.decimal_places),
      })));
    }
  }, [bindingsQ.data]);

  const [filter, setFilter] = useState("");
  const bound = useMemo(() => new Set(rows.map((r) => r.tag_id)), [rows]);
  const candidates = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return allTags
      .filter((t) => !bound.has(t.id) && (!q || t.name.toLowerCase().includes(q)))
      .slice(0, 50);
  }, [allTags, bound, filter]);

  const setRow = (i: number, patch: Partial<Row>) =>
    setRows((s) => s.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const removeRow = (i: number) => setRows((s) => s.filter((_, j) => j !== i));
  const addTag = (t: TagLite) => {
    setRows((s) => [...s, {
      tag_id: t.id, tag_name: t.name, display_name: "",
      data_function: "latest", quality_rule: "all", decimals: "",
    }]);
    setFilter("");
  };
  const [pickerOpen, setPickerOpen] = useState(false);
  const addTags = (tags: { id: number; name: string }[]) => {
    setRows((s) => {
      const have = new Set(s.map((r) => r.tag_id));
      const adds = tags.filter((t) => !have.has(t.id)).map((t) => ({
        tag_id: t.id, tag_name: t.name, display_name: "",
        data_function: "latest", quality_rule: "all", decimals: "",
      }));
      return [...s, ...adds];
    });
  };

  const save = useMutation({
    mutationFn: () => api.put(`/report-config/definitions/${defId}/bindings`, {
      bindings: rows.map((r) => {
        const d = parseInt(r.decimals, 10);
        return {
          tag_id: r.tag_id,
          display_name: r.display_name.trim() || null,
          data_function: r.data_function,
          quality_rule: r.quality_rule,
          decimal_places: Number.isFinite(d) ? d : null,
        };
      }),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["report-bindings", defId] });
      onSaved();
    },
    onError: (e: any) => onError(e?.detail || "Saving bindings failed."),
  });

  const grid = "minmax(0,1.6fr) minmax(0,1.4fr) minmax(0,1.3fr) 64px minmax(0,1.4fr) 32px";

  return (
    <SectionCard
      title="Data — tag bindings"
      subtitle="Tags this report includes, and how each is aggregated over the period window"
      action={
        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending || bindingsQ.isLoading}>
          {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          <span className="ml-1">Save data</span>
        </Button>
      }
    >
      {rows.length === 0 && (
        <div className="text-[12px] mb-2" style={{ color: "var(--ios-gray-1)" }}>
          No tags bound yet. Search below to add some.
        </div>
      )}

      {rows.length > 0 && (
        <div className="rounded-lg overflow-hidden" style={{ border: "0.5px solid var(--card-edge,#ddd)" }}>
          <div className="grid items-center gap-2 px-2.5 py-1.5 text-[11px] font-semibold"
            style={{ gridTemplateColumns: grid, color: "var(--ios-gray-1)", backgroundColor: "var(--bg,#fafafa)" }}>
            <div>Tag</div><div>Function</div><div>Quality</div><div>Dec</div><div>Display name</div><div></div>
          </div>
          {rows.map((r, i) => (
            <div key={r.tag_id} className="grid items-center gap-2 px-2.5 py-1.5"
              style={{ gridTemplateColumns: grid, borderTop: "0.5px solid var(--separator,#eee)" }}>
              <div className="text-[12.5px] truncate" title={r.tag_name}>{r.tag_name}</div>
              <Select value={r.data_function} options={FUNCS} labels={FUNC_LABELS}
                onChange={(v) => setRow(i, { data_function: v })} />
              <Select value={r.quality_rule} options={QUALITY} labels={QUALITY_LABELS}
                onChange={(v) => setRow(i, { quality_rule: v })} />
              <Input value={r.decimals} placeholder="—"
                onChange={(e) => setRow(i, { decimals: e.target.value.replace(/[^0-9]/g, "") })} />
              <Input value={r.display_name} placeholder={r.tag_name}
                onChange={(e) => setRow(i, { display_name: e.target.value })} />
              <button onClick={() => removeRow(i)} title="Remove"
                className="flex items-center justify-center rounded-md"
                style={{ color: "var(--ios-gray-1)" }}>
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="mt-2 flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)}>
          <ListTree className="h-3.5 w-3.5" /><span className="ml-1">Browse &amp; select tags…</span>
        </Button>
        <Input className="flex-1" placeholder="…or quick-search by name" value={filter}
          onChange={(e) => setFilter(e.target.value)} />
      </div>
      {filter && (
        <div className="mt-1 max-h-40 overflow-auto rounded-lg" style={{ border: "0.5px solid var(--card-edge,#ddd)" }}>
          {candidates.map((t) => (
            <button key={t.id} onClick={() => addTag(t)}
              className="block w-full text-left px-3 py-1.5 text-[12.5px] hover:bg-[var(--ios-blue-soft,#e6f0fe)]">
              {t.name}
            </button>
          ))}
          {candidates.length === 0 && (
            <div className="px-3 py-2 text-[12px]" style={{ color: "var(--ios-gray-1)" }}>No matches.</div>
          )}
        </div>
      )}

      <TagTreePicker open={pickerOpen} onClose={() => setPickerOpen(false)}
        onConfirm={addTags} alreadyBound={bound} />

      <div className="mt-3 text-[12px]" style={{ color: "var(--ios-gray-1)" }}>
        Functions aggregate over the report's Period window. With no period set, the report
        is a live snapshot and every tag reads its latest value.
      </div>
    </SectionCard>
  );
}
