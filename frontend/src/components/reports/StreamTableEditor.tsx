/**
 * StreamTableEditor — no-code editor for the `stream_table` block.
 *
 * Rows are measurements, columns are devices/streams (FC A / FC B / Station …).
 * Each cell is independent: a column can bind a *different* register than its
 * neighbour (FC A = STR01_*, FC B = STR02_*), be left intentionally blank, or
 * be unset. Picking a measurement in one column auto-maps the same measurement
 * into the other columns by swapping the stream prefix (STR01 → STR0n); you can
 * override or blank any cell.
 *
 * Scaling: tags are fetched PER DEVICE (one request per column device, each well
 * under the API's 1000-tag cap), so the editor works with any number of flow
 * computers — 2 or 20.
 *
 * Storage: `cells` holds the resolved per-column tag ids (what the renderer
 * uses); `cell_regs` holds the per-column register name (editor display, with
 * the sentinel "__blank__" for an intentional blank). Legacy rows that stored a
 * single shared `reg` are read transparently.
 */
import { useMemo } from "react";
import { useQuery, useQueries } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, Select } from "@/pages/triggers-shared";

type Device = { id: number; name: string; protocol?: string };
type TagLite2 = { id: number; name: string; unit_code: string | null; engineering_unit: string | null };
type Col = { label: string; device_id: number | null };
type Row = {
  label: string; unit: string; decimals: number | null;
  reg?: string; cells: (number | null)[]; cell_regs?: (string | null)[];
  status_regs?: (string | null)[]; status_cells?: (number | null)[];
};
type Section = { name: string; rows: Row[] };

const BLANK = "__blank__";

function humanize(reg: string): string {
  return reg.replace(/^STR\d+_/, "").replace(/_INUSE$/, "").replace(/_\d+$/, "")
    .replace(/_/g, " ").trim();
}

export function StreamTableEditor({ block, onPatch, page, advanced }: { block: any; onPatch: (p: any) => void; page?: { size: string; orientation: string }; advanced?: boolean }) {
  const cols: Col[] = block.columns ?? [];
  const sections: Section[] = block.sections ?? [];

  // ---- data: device list + per-device tags ---------------------------------
  const { data: deviceList = [] } = useQuery({
    queryKey: ["devices", "stream-editor"],
    queryFn: () => api.get<Device[]>("/devices"),
    staleTime: 60_000,
  });
  const devices = useMemo(
    () => [...deviceList].sort((a, b) => a.name.localeCompare(b.name)),
    [deviceList]);

  const deviceIds = useMemo(
    () => Array.from(new Set(cols.map((c) => c.device_id).filter((x): x is number => x != null))),
    [cols]);

  const tagQueries = useQueries({
    queries: deviceIds.map((id) => ({
      queryKey: ["tags", "device", id],
      queryFn: () => api.get<TagLite2[]>(`/tags?device_id=${id}&limit=1000`),
      staleTime: 60_000,
    })),
  });

  // byDevReg[device_id][regName] = {id, unit}. Undefined entry => not loaded yet.
  const sig = deviceIds.map((id, i) => `${id}:${tagQueries[i]?.dataUpdatedAt ?? 0}`).join("|");
  const byDevReg = useMemo(() => {
    const m: Record<number, Record<string, { id: number; unit: string }>> = {};
    deviceIds.forEach((id, i) => {
      const data = tagQueries[i]?.data;
      if (!data) return; // leave undefined => "loading", so cells aren't clobbered
      const r: Record<string, { id: number; unit: string }> = {};
      data.forEach((t) => { r[t.name] = { id: t.id, unit: t.unit_code || t.engineering_unit || "" }; });
      m[id] = r;
    });
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);

  // ---- helpers --------------------------------------------------------------
  const effRegs = (r: Row, cs: Col[]): (string | null)[] => {
    const n = cs.length;
    if (Array.isArray(r.cell_regs) && r.cell_regs.length === n) return r.cell_regs.slice();
    const out: (string | null)[] = new Array(n).fill(r.reg ?? null); // legacy shared reg
    if (Array.isArray(r.cell_regs)) {
      for (let i = 0; i < Math.min(n, r.cell_regs.length); i++) out[i] = r.cell_regs[i];
    }
    return out;
  };

  const effArr = (arr: (string | null)[] | undefined, n: number): (string | null)[] => {
    const out: (string | null)[] = new Array(n).fill(null);
    if (Array.isArray(arr)) for (let i = 0; i < Math.min(n, arr.length); i++) out[i] = arr[i];
    return out;
  };

  const resolveCell = (reg: string | null, deviceId: number | null, existing: number | null | undefined): number | null => {
    if (deviceId == null) return null;
    const d = byDevReg[deviceId];
    if (d === undefined) return existing ?? null;      // tags not loaded → preserve stored id
    if (!reg || reg === BLANK) return null;
    return d[reg]?.id ?? null;
  };

  const autoMap = (reg: string, deviceId: number | null): string | null => {
    if (deviceId == null) return null;
    const d = byDevReg[deviceId];
    if (!d) return null;
    if (d[reg]) return reg;                             // exact match
    const suffix = reg.replace(/^STR\d+_/, "");
    const hit = Object.keys(d).find((k) => k.replace(/^STR\d+_/, "") === suffix);
    return hit ?? null;
  };

  const recompute = (nc: Col[], ns: Section[]): Section[] =>
    ns.map((s) => ({
      ...s,
      rows: s.rows.map((r) => {
        const cr = effRegs(r, nc);
        const out: Row = { ...r, cell_regs: cr, cells: nc.map((c, i) => resolveCell(cr[i], c.device_id, r.cells?.[i])) };
        if (Array.isArray(r.status_regs)) {
          const sr = effArr(r.status_regs, nc.length);
          out.status_regs = sr;
          out.status_cells = nc.map((c, i) => resolveCell(sr[i], c.device_id, r.status_cells?.[i]));
        }
        return out;
      }),
    }));

  const commit = (nc: Col[], ns: Section[]) => onPatch({ columns: nc, sections: recompute(nc, ns) });

  const regOptionsFor = (deviceId: number | null) => {
    const d = deviceId != null ? byDevReg[deviceId] : undefined;
    const names = d ? Object.keys(d).sort() : [];
    return { options: [BLANK, ...names], labels: ["— leave blank —", ...names] };
  };

  const rowStatus = (cr: (string | null)[]) => {
    let resolved = 0, blank = 0, missing = 0, pending = 0;
    cols.forEach((c, i) => {
      if (c.device_id == null) return;
      const reg = cr[i];
      if (reg === BLANK) { blank++; return; }
      const d = byDevReg[c.device_id];
      if (d === undefined) { pending++; return; }
      if (reg && d[reg]) resolved++;
      else missing++;
    });
    return { resolved, blank, missing, pending };
  };

  // ---- column ops -----------------------------------------------------------
  const setCol = (i: number, p: Partial<Col>) => commit(cols.map((c, j) => (j === i ? { ...c, ...p } : c)), sections);
  const addCol = () => {
    const ns = sections.map((s) => ({
      ...s, rows: s.rows.map((r) => { const cr = effRegs(r, cols); cr.push(null);
        const sr = Array.isArray(r.status_regs) ? effArr(r.status_regs, cols.length) : undefined; if (sr) sr.push(null);
        return { ...r, cell_regs: cr, ...(sr ? { status_regs: sr } : {}) }; }),
    }));
    commit([...cols, { label: `FC ${String.fromCharCode(65 + cols.length)}`, device_id: devices[0]?.id ?? null }], ns);
  };
  const delCol = (i: number) => {
    const ns = sections.map((s) => ({
      ...s, rows: s.rows.map((r) => { const cr = effRegs(r, cols); cr.splice(i, 1);
        const sr = Array.isArray(r.status_regs) ? effArr(r.status_regs, cols.length) : undefined; if (sr) sr.splice(i, 1);
        return { ...r, cell_regs: cr, ...(sr ? { status_regs: sr } : {}) }; }),
    }));
    commit(cols.filter((_, j) => j !== i), ns);
  };

  // ---- section / row ops ----------------------------------------------------
  const setSec = (i: number, p: Partial<Section>) => commit(cols, sections.map((s, j) => (j === i ? { ...s, ...p } : s)));
  const setSecBg = (i: number, color: string) => {
    const st: any = { ...((sections[i] as any).style ?? {}) };
    st.background = color ? { type: "solid", color } : undefined;
    setSec(i, { style: st } as any);
  };
  const updSecStyle = (i: number, group: string, key: string, value: any) => {
    const st: any = { ...((sections[i] as any).style ?? {}) };
    st[group] = { ...(st[group] ?? {}), [key]: value };
    setSec(i, { style: st } as any);
  };
  const addSec = () => commit(cols, [...sections, { name: "NEW SECTION", rows: [] }]);
  const delSec = (i: number) => commit(cols, sections.filter((_, j) => j !== i));
  const setRow = (si: number, ri: number, p: Partial<Row>) =>
    commit(cols, sections.map((s, j) => (j !== si ? s : { ...s, rows: s.rows.map((r, k) => (k === ri ? { ...r, ...p } : r)) })));
  const addRow = (si: number) =>
    commit(cols, sections.map((s, j) => (j !== si ? s
      : { ...s, rows: [...s.rows, { label: "", unit: "", decimals: 2, cell_regs: cols.map(() => null), cells: cols.map(() => null) }] })));
  const delRow = (si: number, ri: number) =>
    commit(cols, sections.map((s, j) => (j !== si ? s : { ...s, rows: s.rows.filter((_, k) => k !== ri) })));

  const pickCell = (si: number, ri: number, ci: number, val: string) => {
    const row = sections[si].rows[ri];
    const cr = effRegs(row, cols);
    cr[ci] = val;
    if (val && val !== BLANK) {
      cols.forEach((c, j) => {                          // auto-map into still-unset columns
        if (j === ci || c.device_id == null || cr[j]) return;
        const mapped = autoMap(val, c.device_id);
        if (mapped) cr[j] = mapped;
      });
    }
    const patch: Partial<Row> = { cell_regs: cr };
    if (val && val !== BLANK) {
      const unit = byDevReg[cols[ci].device_id ?? -1]?.[val]?.unit ?? "";
      if (!row.label) patch.label = humanize(val);
      if (!row.unit && unit) patch.unit = `(${unit})`;
    }
    setRow(si, ri, patch);
  };

  const pickStatusCell = (si: number, ri: number, ci: number, val: string) => {
    const row = sections[si].rows[ri];
    const sr = effArr(row.status_regs ?? cols.map(() => null), cols.length);
    sr[ci] = val;
    if (val && val !== BLANK) {
      cols.forEach((c, j) => {
        if (j === ci || c.device_id == null || sr[j]) return;
        const mapped = autoMap(val, c.device_id);
        if (mapped) sr[j] = mapped;
      });
    }
    setRow(si, ri, { status_regs: sr });
  };

  const toggleStatus = (si: number, ri: number, on: boolean) =>
    setRow(si, ri, on ? { status_regs: cols.map(() => null) } : { status_regs: undefined, status_cells: undefined });

  const lbl = { fontSize: 11, color: "var(--ios-gray-1)" } as const;

  // ---- A4/A3 fit estimate ---------------------------------------------------
  const USABLE: Record<string, Record<string, number>> = {
    A4: { portrait: 182, landscape: 269 },
    A3: { portrait: 269, landscape: 392 },
    Letter: { portrait: 190, landscape: 267 },
    Legal: { portrait: 190, landscape: 343 },
  };
  const pgSize = page?.size ?? "A4";
  const pgOri = page?.orientation ?? "portrait";
  const usable = (USABLE[pgSize] ?? USABLE.A4)[pgOri] ?? 182;
  const FIXED_MM = 70, PER_COL_MM = 30;     // label+unit, then per stream column
  const maxCols = Math.max(1, Math.floor((usable - FIXED_MM) / PER_COL_MM));
  const overflow = cols.length > maxCols;
  const fitMsg = overflow
    ? `${cols.length} columns may overflow ${pgSize} ${pgOri} (fits ~${maxCols}). Use landscape / A3, or split into two stream tables.`
    : `${cols.length} of ~${maxCols} columns that fit ${pgSize} ${pgOri}.`;

  return (
    <div className="space-y-4">
      <div className="rounded-md px-2 py-1 text-[11px]"
        style={{ background: overflow ? "#FEF3C7" : "transparent",
                 color: overflow ? "#92400E" : "var(--ios-gray-1)",
                 border: overflow ? "0.5px solid #FCD34D" : "0.5px solid var(--separator)" }}>
        {overflow ? "\u26A0 " : ""}{fitMsg}
      </div>
      <Field label="Table title (optional)">
        <Input value={block.title ?? ""} onChange={(e) => onPatch({ title: e.target.value })} placeholder="e.g. CURRENT DATA" />
      </Field>

      {/* Columns */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span style={lbl}>Columns (one per device / stream — polled or computed)</span>
          <Button variant="ghost" size="sm" onClick={addCol}><Plus className="h-3.5 w-3.5" /> Column</Button>
        </div>
        <div className="space-y-2">
          {cols.map((c, i) => (
            <div key={i} className="flex items-end gap-2">
              <Field label="Heading"><Input value={c.label} onChange={(e) => setCol(i, { label: e.target.value })} /></Field>
              <Field label="Device">
                <Select value={String(c.device_id ?? "")}
                  options={devices.map((d) => String(d.id))}
                  labels={devices.map((d) => d.name)}
                  onChange={(v) => setCol(i, { device_id: v ? Number(v) : null })} />
              </Field>
              <Button variant="ghost" size="sm" onClick={() => delCol(i)} disabled={cols.length <= 1}><Trash2 className="h-3.5 w-3.5" /></Button>
            </div>
          ))}
        </div>
      </div>

      {/* Sections + rows */}
      <div className="space-y-3">
        {sections.map((s, si) => (
          <div key={si} className="rounded-lg p-2" style={{ border: "1px solid var(--separator)" }}>
            <div className="flex items-end gap-2 mb-1">
              <Field label="Section heading"><Input value={s.name} onChange={(e) => setSec(si, { name: e.target.value })} /></Field>
              <Button variant="ghost" size="sm" onClick={() => delSec(si)}><Trash2 className="h-3.5 w-3.5" /></Button>
            </div>
            {advanced && (() => {
              const ss: any = (s as any).style ?? {};
              const hex = (c?: string) => (/^#[0-9a-fA-F]{6}$/.test(c ?? "") ? (c as string) : "#000000");
              return (
                <div className="flex items-center gap-3 flex-wrap mb-2" style={lbl}>
                  <span>Section style:</span>
                  <span className="inline-flex items-center gap-1">bg
                    <input type="color" value={hex(ss.background?.color)} onChange={(e) => setSecBg(si, e.target.value)}
                      style={{ width: 24, height: 20, border: "0.5px solid var(--separator)", borderRadius: 4, padding: 0 }} />
                    {ss.background?.color && <button type="button" onClick={() => setSecBg(si, "")}>×</button>}
                  </span>
                  <span className="inline-flex items-center gap-1">text
                    <input type="color" value={hex(ss.text?.color)} onChange={(e) => updSecStyle(si, "text", "color", e.target.value)}
                      style={{ width: 24, height: 20, border: "0.5px solid var(--separator)", borderRadius: 4, padding: 0 }} />
                    {ss.text?.color && <button type="button" onClick={() => updSecStyle(si, "text", "color", undefined)}>×</button>}
                  </span>
                  <label className="inline-flex items-center gap-1" style={{ color: "var(--text-primary)" }}>
                    <input type="checkbox" checked={ss.font?.weight === "bold"}
                      onChange={(e) => updSecStyle(si, "font", "weight", e.target.checked ? "bold" : undefined)} /> bold
                  </label>
                  <label className="inline-flex items-center gap-1" style={{ color: "var(--text-primary)" }}>
                    <input type="checkbox" checked={ss.font?.case === "uppercase"}
                      onChange={(e) => updSecStyle(si, "font", "case", e.target.checked ? "uppercase" : undefined)} /> CAPS
                  </label>
                </div>
              );
            })()}
            <div className="space-y-3">
              {s.rows.map((r, ri) => {
                const cr = effRegs(r, cols);
                const st = rowStatus(cr);
                const ind = st.pending > 0 ? "…"
                  : `${st.resolved}\u2713${st.blank ? ` · ${st.blank} blank` : ""}${st.missing ? ` · ${st.missing} missing` : ""}`;
                const indColor = st.missing > 0 ? "#c00" : "var(--ios-gray-1)";
                return (
                  <div key={ri} className="rounded-md p-2" style={{ border: "0.5px solid var(--separator)" }}>
                    <div className="flex items-end gap-2">
                      <Field label="Label"><Input value={r.label} onChange={(e) => setRow(si, ri, { label: e.target.value })} /></Field>
                      <Field label="Unit"><Input value={r.unit} onChange={(e) => setRow(si, ri, { unit: e.target.value })} /></Field>
                      <Field label="Dec">
                        <Input type="number" value={r.decimals ?? ""} style={{ width: 56 }}
                          onChange={(e) => setRow(si, ri, { decimals: e.target.value === "" ? null : Number(e.target.value) })} />
                      </Field>
                      <span style={{ fontSize: 10, color: indColor, whiteSpace: "nowrap" }}>{ind}</span>
                      <Button variant="ghost" size="sm" title="Pair a status-flag tag per column"
                        onClick={() => toggleStatus(si, ri, !Array.isArray(r.status_regs))}>
                        {Array.isArray(r.status_regs) ? "\u2212 flag" : "+ flag"}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => delRow(si, ri)}><Trash2 className="h-3.5 w-3.5" /></Button>
                    </div>
                    <div className="grid gap-2 mt-1" style={{ gridTemplateColumns: `repeat(${Math.max(cols.length, 1)}, 1fr)` }}>
                      {cols.map((c, ci) => {
                        const opt = regOptionsFor(c.device_id);
                        return (
                          <Field key={ci} label={`${c.label || `Col ${ci + 1}`} measurement`}>
                            <Select value={cr[ci] ?? ""} options={opt.options} labels={opt.labels}
                              onChange={(v) => pickCell(si, ri, ci, v)} />
                          </Field>
                        );
                      })}
                    </div>
                    {Array.isArray(r.status_regs) && (
                      <div className="grid gap-2 mt-1" style={{ gridTemplateColumns: `repeat(${Math.max(cols.length, 1)}, 1fr)` }}>
                        {cols.map((c, ci) => {
                          const opt = regOptionsFor(c.device_id);
                          const sr = effArr(r.status_regs, cols.length);
                          return (
                            <Field key={ci} label={`${c.label || `Col ${ci + 1}`} status flag`}>
                              <Select value={sr[ci] ?? ""} options={opt.options} labels={opt.labels}
                                onChange={(v) => pickStatusCell(si, ri, ci, v)} />
                            </Field>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
              <Button variant="ghost" size="sm" onClick={() => addRow(si)}><Plus className="h-3.5 w-3.5" /> Measurement</Button>
            </div>
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={addSec}><Plus className="h-3.5 w-3.5" /> Section</Button>
      </div>
    </div>
  );
}
