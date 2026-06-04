/**
 * BI Explorer — scoped Power BI / Tableau-style data exploration.
 *
 * Drag fields onto shelves (Rows = measures, Columns = X dimension,
 * Color = split dimension, Filters), pick a chart in "Show me", and the
 * view rebuilds live by POSTing a spec to /api/bi/query.
 *
 * Backend: GET /api/bi/model (field list) + POST /api/bi/query (results).
 * Charts are rendered as lightweight inline SVG/HTML so no new dependency is
 * needed; styling uses the app's iOS design tokens for a native feel.
 */
import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  BarChart3, LineChart, Table2, Hash, Filter as FilterIcon,
  Database, Play, X, GripVertical, Loader2,
} from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/page-header";

/* ---------------- types mirroring /api/bi ---------------- */
type Dim = { key: string; label: string; kind: "dimension" | "time" };
type Model = {
  dimensions: Dim[];
  measures: { agg: string }[];
  value_field: { key: string; label: string };
  filter_values: Record<string, string[]>;
};
type QueryResult = {
  dimensions: { key: string; label: string }[];
  measures: { agg: string; label: string }[];
  rows: Record<string, any>[];
  row_count: number;
};

type ShelfMeasure = { agg: string; label: string };
type Shelves = {
  rows: ShelfMeasure[];     // measures (Y)
  cols: string[];           // dimension keys (X)
  color: string[];          // dimension key (split) — we use [0]
  filters: { field: string; op: string; value?: any; values?: any[] }[];
};

const AGGS = ["avg", "sum", "min", "max", "first", "last", "count"];
const CHART_COLORS = [
  "var(--ios-blue)", "var(--ios-green)", "var(--ios-orange)",
  "var(--ios-purple)", "var(--ios-teal)", "var(--ios-red)", "var(--ios-indigo)",
];

type ChartKind = "bar" | "line" | "table" | "kpi";

export default function Explorer() {
  const model = useQuery({ queryKey: ["bi-model"], queryFn: () => api.get<Model>("/bi/model") });

  const [shelves, setShelves] = useState<Shelves>({
    rows: [{ agg: "avg", label: "Avg Value" }],
    cols: ["time:hour"],
    color: [],
    filters: [],
  });
  const [chart, setChart] = useState<ChartKind>("bar");
  const [dragField, setDragField] = useState<{ kind: "dim" | "measure"; key: string } | null>(null);

  const runQuery = useMutation({
    mutationFn: (spec: any) => api.post<QueryResult>("/bi/query", spec),
  });

  const dims = model.data?.dimensions ?? [];

  /* ----- build the spec the backend expects ----- */
  function buildSpec(): any {
    const dimsList = [...shelves.cols, ...shelves.color];
    return {
      measures: shelves.rows.map((m) => ({ agg: m.agg, label: m.label })),
      dimensions: dimsList,
      filters: shelves.filters,
      limit: 5000,
    };
  }
  function run() {
    if (shelves.rows.length === 0) return;
    runQuery.mutate(buildSpec());
  }

  /* ----- drag-drop handlers ----- */
  function onDropTo(target: keyof Shelves) {
    if (!dragField) return;
    setShelves((s) => {
      const next = { ...s };
      if (target === "rows" && dragField.kind === "measure") {
        next.rows = [...s.rows, { agg: "avg", label: `avg(${model.data?.value_field.label})` }];
      } else if ((target === "cols" || target === "color") && dragField.kind === "dim") {
        if (!s[target].includes(dragField.key)) next[target] = [...s[target], dragField.key];
        if (target === "color") next.color = [dragField.key]; // single split
      } else if (target === "filters" && dragField.kind === "dim") {
        next.filters = [...s.filters, { field: dragField.key, op: "in", values: [] }];
      }
      return next;
    });
    setDragField(null);
  }
  function removeFrom(target: keyof Shelves, idx: number) {
    setShelves((s) => {
      const next = { ...s };
      (next[target] as any[]) = (s[target] as any[]).filter((_, i) => i !== idx);
      return next;
    });
  }
  function setMeasureAgg(idx: number, agg: string) {
    setShelves((s) => {
      const rows = [...s.rows];
      rows[idx] = { ...rows[idx], agg, label: `${agg}(${model.data?.value_field.label})` };
      return { ...s, rows };
    });
  }

  const dimLabel = (k: string) => dims.find((d) => d.key === k)?.label ?? k;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Data Explorer"
        subtitle="Drag fields onto shelves to explore your tag data — bars, trends, tables and KPIs"
      />

      <div className="grid gap-4" style={{ gridTemplateColumns: "210px 230px 1fr" }}>
        {/* ---------- FIELDS ---------- */}
        <div className="rounded-xl border p-3" style={panel}>
          <div style={sectLbl}><Database className="inline h-3 w-3 mr-1" /> Fields</div>
          {model.isLoading && <div style={muted}>Loading…</div>}
          <div className="mt-2 space-y-1">
            <div style={{ ...muted, fontSize: 10, marginTop: 6 }}>Measures</div>
            <FieldChip
              label={model.data?.value_field.label ?? "Tag Value"}
              kind="measure"
              onDrag={() => setDragField({ kind: "measure", key: "value" })}
            />
            <div style={{ ...muted, fontSize: 10, marginTop: 10 }}>Dimensions</div>
            {dims.map((d) => (
              <FieldChip
                key={d.key}
                label={d.label}
                kind="dim"
                isTime={d.kind === "time"}
                onDrag={() => setDragField({ kind: "dim", key: d.key })}
              />
            ))}
          </div>
          <div style={{ ...muted, fontSize: 10.5, marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--separator)", lineHeight: 1.5 }}>
            <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "var(--ios-green)", marginRight: 5 }} />
            Dimension — group/split by<br />
            <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: "var(--ios-blue)", marginRight: 5 }} />
            Measure — aggregated number
          </div>
        </div>

        {/* ---------- SHELVES ---------- */}
        <div className="rounded-xl border p-3" style={{ ...panel, background: "var(--bg-grouped)" }}>
          <Shelf label="Rows · measures (Y)" onDrop={() => onDropTo("rows")}>
            {shelves.rows.map((m, i) => (
              <MeasurePill key={i} agg={m.agg} valueLabel={model.data?.value_field.label ?? "Value"}
                onAgg={(a) => setMeasureAgg(i, a)} onRemove={() => removeFrom("rows", i)} />
            ))}
          </Shelf>
          <Shelf label="Columns · dimension (X)" onDrop={() => onDropTo("cols")}>
            {shelves.cols.map((d, i) => (
              <DimPill key={i} label={dimLabel(d)} onRemove={() => removeFrom("cols", i)} />
            ))}
          </Shelf>
          <Shelf label="Color · split" onDrop={() => onDropTo("color")}>
            {shelves.color.map((d, i) => (
              <DimPill key={i} label={dimLabel(d)} onRemove={() => removeFrom("color", i)} />
            ))}
          </Shelf>
          <Shelf label="Filters" onDrop={() => onDropTo("filters")} icon={<FilterIcon className="h-3 w-3" />}>
            {shelves.filters.map((f, i) => (
              <DimPill key={i} label={`${dimLabel(f.field)}`} onRemove={() => removeFrom("filters", i)} />
            ))}
          </Shelf>

          {/* Show me */}
          <div style={{ ...sectLbl, marginTop: 14 }}>Show me</div>
          <div className="grid grid-cols-4 gap-1.5 mt-1">
            {([["bar", BarChart3], ["line", LineChart], ["table", Table2], ["kpi", Hash]] as const).map(
              ([k, Icon]) => (
                <button key={k} onClick={() => setChart(k)} title={k}
                  className="flex items-center justify-center rounded-md border py-2"
                  style={{
                    borderColor: chart === k ? "var(--ios-blue)" : "var(--separator)",
                    background: chart === k ? "var(--ios-blue-soft)" : "var(--surface)",
                    color: chart === k ? "var(--ios-blue)" : "var(--text-secondary)",
                  }}>
                  <Icon className="h-4 w-4" />
                </button>
              ))}
          </div>

          <button onClick={run} disabled={runQuery.isPending || shelves.rows.length === 0}
            className="mt-3 w-full rounded-lg py-2 font-semibold text-white flex items-center justify-center gap-1.5"
            style={{ background: "var(--ios-blue)", opacity: shelves.rows.length === 0 ? 0.5 : 1, fontSize: 13 }}>
            {runQuery.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Run
          </button>
        </div>

        {/* ---------- VIZ ---------- */}
        <div className="rounded-xl border p-4" style={panel}>
          {!runQuery.data && !runQuery.isPending && (
            <div style={{ ...muted, textAlign: "center", padding: "60px 0" }}>
              Configure the shelves and press <b>Run</b> to see your data.
            </div>
          )}
          {runQuery.isError && (
            <div style={{ color: "var(--ios-red)", fontSize: 13 }}>
              {(runQuery.error as any)?.message ?? "Query failed."}
            </div>
          )}
          {runQuery.data && <Viz kind={chart} data={runQuery.data} />}
        </div>
      </div>
    </div>
  );
}

/* ===================== chart renderers ===================== */
function Viz({ kind, data }: { kind: ChartKind; data: QueryResult }) {
  if (data.rows.length === 0)
    return <div style={{ ...muted, textAlign: "center", padding: 40 }}>No data for this selection.</div>;
  if (kind === "table") return <ResultTable data={data} />;
  if (kind === "kpi") return <KpiView data={data} />;
  if (kind === "line") return <SeriesChart data={data} mode="line" />;
  return <SeriesChart data={data} mode="bar" />;
}

/* group rows by X (dim0) and split series by Color (dim1 if present) */
function pivot(data: QueryResult) {
  const hasColor = data.dimensions.length > 1;
  const xKey = "dim0", colorKey = "dim1", mKey = "m0";
  const xs: string[] = [];
  const seriesMap = new Map<string, Map<string, number>>(); // series -> (x -> value)
  for (const r of data.rows) {
    const x = String(r[xKey] ?? "—");
    const s = hasColor ? String(r[colorKey] ?? "—") : (data.measures[0]?.label ?? "value");
    if (!xs.includes(x)) xs.push(x);
    if (!seriesMap.has(s)) seriesMap.set(s, new Map());
    seriesMap.get(s)!.set(x, Number(r[mKey] ?? 0));
  }
  return { xs, series: [...seriesMap.entries()].map(([name, m]) => ({ name, m })) };
}

function SeriesChart({ data, mode }: { data: QueryResult; mode: "bar" | "line" }) {
  const { xs, series } = useMemo(() => pivot(data), [data]);
  const W = 720, H = 320, padL = 48, padB = 46, padT = 16, padR = 16;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const allVals = series.flatMap((s) => [...s.m.values()]);
  const maxV = Math.max(1, ...allVals), minV = Math.min(0, ...allVals);
  const yOf = (v: number) => padT + plotH - ((v - minV) / (maxV - minV || 1)) * plotH;
  const xStep = plotW / Math.max(1, xs.length);
  const xCenter = (i: number) => padL + xStep * i + xStep / 2;

  const ticks = 4;
  const yTicks = Array.from({ length: ticks + 1 }, (_, i) => minV + ((maxV - minV) * i) / ticks);

  return (
    <div>
      <Legend series={series} />
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: W }}>
        {/* y grid + labels */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={padL} y1={yOf(v)} x2={W - padR} y2={yOf(v)} stroke="var(--separator)" strokeWidth={0.5} />
            <text x={padL - 6} y={yOf(v) + 3} fontSize={9} fill="var(--text-tertiary)" textAnchor="end">
              {fmtNum(v)}
            </text>
          </g>
        ))}
        {/* x labels */}
        {xs.map((x, i) => (
          <text key={i} x={xCenter(i)} y={H - padB + 16} fontSize={9} fill="var(--text-secondary)"
            textAnchor="middle">{shortLbl(x)}</text>
        ))}
        {mode === "bar"
          ? series.map((s, si) =>
              xs.map((x, xi) => {
                const v = s.m.get(x) ?? 0;
                const bw = (xStep * 0.7) / series.length;
                const bx = xCenter(xi) - (xStep * 0.35) + si * bw;
                return (
                  <rect key={`${si}-${xi}`} x={bx} y={yOf(v)} width={Math.max(1, bw - 2)}
                    height={Math.max(0, yOf(minV) - yOf(v))} rx={2}
                    fill={CHART_COLORS[si % CHART_COLORS.length]} />
                );
              }))
          : series.map((s, si) => {
              const pts = xs.map((x, xi) => `${xCenter(xi)},${yOf(s.m.get(x) ?? 0)}`).join(" ");
              return <polyline key={si} points={pts} fill="none"
                stroke={CHART_COLORS[si % CHART_COLORS.length]} strokeWidth={2}
                strokeLinejoin="round" strokeLinecap="round" />;
            })}
      </svg>
    </div>
  );
}

function Legend({ series }: { series: { name: string }[] }) {
  if (series.length <= 1) return null;
  return (
    <div className="flex flex-wrap gap-3 mb-2" style={{ fontSize: 11 }}>
      {series.map((s, i) => (
        <span key={i} className="inline-flex items-center gap-1.5">
          <i style={{ width: 11, height: 11, borderRadius: 3, background: CHART_COLORS[i % CHART_COLORS.length], display: "inline-block" }} />
          <span style={{ color: "var(--text-secondary)" }}>{s.name}</span>
        </span>
      ))}
    </div>
  );
}

function ResultTable({ data }: { data: QueryResult }) {
  return (
    <div className="overflow-auto">
      <table className="w-full" style={{ borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead>
          <tr>
            {data.dimensions.map((d, i) => <th key={`d${i}`} style={th}>{d.label}</th>)}
            {data.measures.map((m, i) => <th key={`m${i}`} style={{ ...th, textAlign: "right" }}>{m.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r, ri) => (
            <tr key={ri}>
              {data.dimensions.map((_, i) => <td key={`d${i}`} style={td}>{shortLbl(String(r[`dim${i}`] ?? "—"))}</td>)}
              {data.measures.map((_, i) => <td key={`m${i}`} style={{ ...td, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{fmtNum(Number(r[`m${i}`]))}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ ...muted, fontSize: 11, marginTop: 8 }}>{data.row_count} rows</div>
    </div>
  );
}

function KpiView({ data }: { data: QueryResult }) {
  // KPI = the single aggregate of m0 across all rows (or the first row).
  const vals = data.rows.map((r) => Number(r["m0"] ?? 0));
  const total = vals.reduce((a, b) => a + b, 0);
  const single = data.rows.length === 1 ? vals[0] : total;
  return (
    <div className="flex flex-col items-center justify-center" style={{ padding: "50px 0" }}>
      <div style={{ fontSize: 52, fontWeight: 800, color: "var(--ios-blue-on-soft)", lineHeight: 1 }}>
        {fmtNum(single)}
      </div>
      <div style={{ ...muted, marginTop: 10 }}>
        {data.measures[0]?.label ?? "value"}{data.rows.length > 1 ? ` · sum of ${data.rows.length} groups` : ""}
      </div>
    </div>
  );
}

/* ===================== small pieces ===================== */
function FieldChip({ label, kind, isTime, onDrag }: { label: string; kind: "dim" | "measure"; isTime?: boolean; onDrag: () => void }) {
  return (
    <div draggable onDragStart={onDrag}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 cursor-grab select-none"
      style={{ border: "1px solid transparent", fontSize: 12.5, fontWeight: 600 }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--ios-blue-soft)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      <span style={{
        width: 8, height: 8, flex: "0 0 auto",
        borderRadius: kind === "measure" ? "50%" : 2,
        background: kind === "measure" ? "var(--ios-blue)" : "var(--ios-green)",
      }} />
      {label}{isTime ? <span style={{ ...muted, fontSize: 10 }}> · time</span> : null}
    </div>
  );
}

function Shelf({ label, icon, children, onDrop }: { label: string; icon?: React.ReactNode; children: React.ReactNode; onDrop: () => void }) {
  const [over, setOver] = useState(false);
  return (
    <div className="mb-3">
      <div style={{ ...sectLbl, marginBottom: 4 }}>{icon} {label}</div>
      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); onDrop(); }}
        className="rounded-lg p-1.5 flex flex-wrap gap-1.5"
        style={{
          minHeight: 38, background: "var(--surface)",
          border: `1.5px dashed ${over ? "var(--ios-blue)" : "var(--separator)"}`,
        }}>
        {children}
      </div>
    </div>
  );
}

function MeasurePill({ agg, valueLabel, onAgg, onRemove }: { agg: string; valueLabel: string; onAgg: (a: string) => void; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5"
      style={{ background: "var(--ios-blue-soft)", color: "var(--ios-blue-on-soft)", fontSize: 11, fontWeight: 600 }}>
      <select value={agg} onChange={(e) => onAgg(e.target.value)}
        style={{ background: "var(--ios-blue)", color: "#fff", border: "none", borderRadius: 3, fontSize: 9, fontWeight: 700, padding: "0 2px" }}>
        {AGGS.map((a) => <option key={a} value={a}>{a.toUpperCase()}</option>)}
      </select>
      {valueLabel}
      <X className="h-3 w-3 cursor-pointer" onClick={onRemove} />
    </span>
  );
}

function DimPill({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md px-2 py-0.5"
      style={{ background: "var(--ios-green-soft)", color: "var(--ios-green-on-soft)", fontSize: 11, fontWeight: 600 }}>
      <GripVertical className="h-3 w-3 opacity-50" />
      {label}
      <X className="h-3 w-3 cursor-pointer" onClick={onRemove} />
    </span>
  );
}

/* ===================== helpers / styles ===================== */
const panel: React.CSSProperties = {
  background: "var(--surface)", borderColor: "var(--separator)", boxShadow: "var(--card-shadow)",
};
const sectLbl: React.CSSProperties = {
  fontSize: 10.5, textTransform: "uppercase", letterSpacing: 0.4, fontWeight: 700, color: "var(--text-secondary)",
};
const muted: React.CSSProperties = { color: "var(--text-secondary)", fontSize: 12.5 };
const th: React.CSSProperties = {
  background: "var(--ios-blue-on-soft)", color: "#fff", textAlign: "left",
  padding: "5px 8px", fontSize: 10, fontWeight: 600, position: "sticky", top: 0,
};
const td: React.CSSProperties = { borderBottom: "0.5px solid var(--separator)", padding: "5px 8px" };

function fmtNum(v: number): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
  return Number(v.toPrecision(5)).toLocaleString(undefined, { maximumFractionDigits: 4 });
}
function shortLbl(s: string): string {
  // ISO timestamps -> HH:MM or date; otherwise truncate.
  const d = new Date(s);
  if (!Number.isNaN(d.getTime()) && /\d{4}-\d{2}-\d{2}/.test(s)) {
    return s.includes("T") || s.includes(" ")
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { month: "short", day: "numeric" });
  }
  return s.length > 14 ? s.slice(0, 13) + "…" : s;
}
