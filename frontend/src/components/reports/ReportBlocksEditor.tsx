/**
 * ReportBlocksEditor (TC-2) — the no-code report template builder.
 *
 * Edits the ordered `template_blocks` list that the backend compiler
 * (services/report_blocks.py) turns into the report's HTML/PDF. The user never
 * writes Jinja2: they add typed blocks (header / text / tag table / KPI row /
 * spacer / page break / raw) and configure each one with simple inputs.
 *
 * Every block gets a STABLE string id on creation so the compiler's precomputed
 * tables/charts keys line up and reordering never breaks bindings.
 *
 * Controlled component: holds nothing of its own — parent owns `value` and gets
 * the new list via onChange. Save/preview live in the parent (ReportsConfig).
 */
import { useState } from "react";
import {
  Plus, Trash2, ChevronUp, ChevronDown, Copy, ChevronRight, GripVertical,
  Heading, PanelTop, PanelBottom, Palette, Type, Table, Gauge, Table2,
  BarChart3, Columns, Minus, SeparatorHorizontal, Code, Square,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, Select, type TagLite } from "@/pages/triggers-shared";
import { StreamTableEditor } from "@/components/reports/StreamTableEditor";

export type Block = { id: string; type: string; [k: string]: any };
type Column = { key: string; label: string; kind: "data" | "formula"; formula?: string; fmt?: string };

// Block types offered in the palette (columns/chart kept minimal for now —
// chart rendering is pending TC-3; nested columns is a later polish).
const PALETTE: { type: string; label: string; hint: string }[] = [
  { type: "header", label: "Header", hint: "Title + generated timestamp" },
  { type: "page_header", label: "Page header", hint: "Repeats top of every page (3 slots)" },
  { type: "page_footer", label: "Page footer", hint: "Repeats bottom of every page (3 slots)" },
  { type: "report_style", label: "Report style override (optional)", hint: "Override the global default for THIS report; blank fields inherit global" },
  { type: "text", label: "Text", hint: "Paragraph; use {tag:Name} inline" },
  { type: "tag_table", label: "Tag table", hint: "One row per bound tag + calc columns" },
  { type: "stream_table", label: "Stream table", hint: "Compare devices side-by-side (FC A / FC B)" },
  { type: "kpi_row", label: "KPI row", hint: "Big single-value tiles" },
  { type: "chart", label: "Chart", hint: "Rendering pending (TC-3)" },
  { type: "spacer", label: "Spacer", hint: "Vertical gap" },
  { type: "page_break", label: "Page break", hint: "Start a new page" },
  { type: "raw", label: "Raw HTML", hint: "Advanced: verbatim HTML/Jinja2" },
];
const DATA_KEYS = ["name", "value", "display", "unit", "quality"];
const DATA_KEY_LABELS = ["Tag name", "Aggregated value", "Value (formatted)", "Unit", "Quality"];

function shortId(type: string): string {
  return `${type}_${Math.random().toString(36).slice(2, 8)}`;
}

function defaults(type: string): Block {
  const id = shortId(type);
  switch (type) {
    case "header": return { id, type, title_source: "report_name", custom_title: "", show_logo: false, show_generated: true };
    case "page_header": return { id, type, left: "{report_title}", center: "", right: "{page_of}" };
    case "page_footer": return { id, type, left: "Generated {generated_at}", center: "", right: "{timezone}" };
    case "report_style": return { id, type,
      theme: { font_family: "Segoe UI", font_size_pt: 10, text_color: "#111827",
        heading_color: "#0B3A67", table_header_bg: "#0B3A67", table_header_fg: "#FFFFFF", alt_row: "#F8FAFC" },
      time: { basis: "system", format: "%d-%b-%Y %H:%M", show_suffix: true } };
    case "text": return { id, type, content: "" };
    case "tag_table": return { id, type, columns: [
      { key: "name", label: "Tag", kind: "data" },
      { key: "display", label: "Value", kind: "data" },
      { key: "unit", label: "Unit", kind: "data" },
    ] as Column[] };
    case "kpi_row": return { id, type, items: [] };
    case "stream_table": return { id, type, title: "", columns: [
      { label: "FC A", device_id: null }, { label: "FC B", device_id: null },
    ], sections: [{ name: "SECTION", rows: [] }] };
    case "chart": return { id, type, title: "" };
    case "spacer": return { id, type, height_mm: 6 };
    case "page_break": return { id, type };
    case "raw": return { id, type, html: "" };
    default: return { id, type };
  }
}

const BLOCK_ICON: Record<string, { Icon: any; color: string }> = {
  header: { Icon: Heading, color: "#2563EB" },
  page_header: { Icon: PanelTop, color: "#0891B2" },
  page_footer: { Icon: PanelBottom, color: "#0891B2" },
  report_style: { Icon: Palette, color: "#7C3AED" },
  text: { Icon: Type, color: "#64748B" },
  tag_table: { Icon: Table, color: "#16A34A" },
  kpi_row: { Icon: Gauge, color: "#EA580C" },
  stream_table: { Icon: Table2, color: "#4F46E5" },
  chart: { Icon: BarChart3, color: "#DB2777" },
  columns: { Icon: Columns, color: "#0D9488" },
  spacer: { Icon: Minus, color: "#94A3B8" },
  page_break: { Icon: SeparatorHorizontal, color: "#D97706" },
  raw: { Icon: Code, color: "#334155" },
};

function BlockIcon({ type, size = 14 }: { type: string; size?: number }) {
  const e = BLOCK_ICON[type] ?? { Icon: Square, color: "#94A3B8" };
  const I = e.Icon;
  return (
    <span className="inline-flex items-center justify-center rounded-md shrink-0"
      style={{ width: size + 12, height: size + 12, background: e.color + "1F", color: e.color }}>
      <I style={{ width: size, height: size }} />
    </span>
  );
}

function summarize(b: any): string {
  switch (b.type) {
    case "text": return (b.content || "").slice(0, 40) || "empty";
    case "header": return b.title_source === "custom" ? (b.custom_title || "custom title") : "report name";
    case "page_header":
    case "page_footer": return [b.left, b.center, b.right].filter(Boolean).join(" · ") || "empty";
    case "report_style": return "theme / time override";
    case "stream_table": return `${(b.columns || []).length} cols · ${(b.sections || []).length} sections`;
    case "tag_table": return `${(b.columns || []).length} columns`;
    case "kpi_row": return `${(b.items || []).length} tiles`;
    case "spacer": return `${b.height_mm ?? ""}mm`;
    default: return b.type;
  }
}

export function ReportBlocksEditor({
  value, onChange, allTags, page,
}: { value: Block[]; onChange: (b: Block[]) => void; allTags: TagLite[]; page?: { size: string; orientation: string } }) {
  const blocks = Array.isArray(value) ? value : [];
  const [adding, setAdding] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  // Drag-to-reorder state: index being dragged, and the current drop target.
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);
  const toggleOpen = (id: string) => setOpen((o) => ({ ...o, [id]: !o[id] }));
  // Page geometry is a per-report property (Settings tab), not part of the
  // theme cascade — the stream-table overflow check uses it directly.
  const pageSettings = { size: page?.size ?? "A4", orientation: page?.orientation ?? "portrait" };

  const add = (type: string) => { const nb = defaults(type); onChange([...blocks, nb]); setOpen((o) => ({ ...o, [nb.id]: true })); setAdding(false); };
  const update = (i: number, patch: Partial<Block>) =>
    onChange(blocks.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const remove = (i: number) => onChange(blocks.filter((_, j) => j !== i));
  const duplicate = (i: number) =>
    onChange([...blocks.slice(0, i + 1), { ...blocks[i], id: shortId(blocks[i].type) }, ...blocks.slice(i + 1)]);
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= blocks.length) return;
    const next = blocks.slice();
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  // Reorder by drag: pull `from` out and re-insert at `to`.
  const moveTo = (from: number, to: number) => {
    if (from === to || from < 0 || to < 0 || from >= blocks.length || to >= blocks.length) return;
    const next = blocks.slice();
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
  };

  return (
    <div className="space-y-2">
      {/* Basic / Advanced + expand-all */}
      <div className="flex items-center justify-between">
        <div className="inline-flex rounded-lg p-0.5" style={{ border: "0.5px solid var(--separator)", backgroundColor: "var(--bg-grouped)" }}>
          {([["basic", "Basic"], ["adv", "Advanced"]] as const).map(([k, label]) => {
            const on = (k === "adv") === advanced;
            return (
              <button key={k} type="button" onClick={() => setAdvanced(k === "adv")}
                className="px-3 py-1 rounded-md text-[12px] font-medium"
                style={on ? { backgroundColor: "var(--bg-elevated)", color: "var(--ios-blue)" } : { color: "var(--ios-gray-1)" }}>
                {label}
              </button>
            );
          })}
        </div>
        {blocks.length > 0 && (
          <button type="button" className="text-[11px]" style={{ color: "var(--ios-blue)" }}
            onClick={() => {
              const allOpen = blocks.every((b) => open[b.id]);
              setOpen(Object.fromEntries(blocks.map((b) => [b.id, !allOpen])));
            }}>
            {blocks.every((b) => open[b.id]) ? "Collapse all" : "Expand all"}
          </button>
        )}
      </div>
      {!advanced && (
        <p className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
          Basic mode hides per-block fonts/colors, banding and section styling. Switch to Advanced to fine-tune.
        </p>
      )}

      {blocks.length === 0 && (
        <div className="rounded-lg p-4 text-center text-[12px]"
          style={{ border: "1px dashed var(--separator)", color: "var(--ios-gray-1)" }}>
          No blocks yet. Add a Header, then a Tag table or KPI row.
        </div>
      )}

      {blocks.map((b, i) => {
        const isOpen = !!open[b.id];
        return (
        <div key={b.id} className="rounded-lg overflow-hidden"
          style={{
            border: overIdx === i && dragIdx !== null && dragIdx !== i
              ? "1px solid var(--ios-blue)" : "0.5px solid var(--separator)",
            backgroundColor: "var(--bg-elevated)",
            opacity: dragIdx === i ? 0.45 : 1,
          }}
          onDragOver={(e) => { if (dragIdx !== null) { e.preventDefault(); if (overIdx !== i) setOverIdx(i); } }}
          onDrop={(e) => {
            e.preventDefault();
            if (dragIdx !== null && dragIdx !== i) moveTo(dragIdx, i);
            setDragIdx(null); setOverIdx(null);
          }}>
          {/* block header bar (click to expand/collapse) */}
          <div className="flex items-center gap-2 px-3 py-2 cursor-pointer"
            style={{ borderBottom: isOpen ? "0.5px solid var(--separator)" : "none", backgroundColor: "var(--bg-grouped)" }}
            onClick={() => toggleOpen(b.id)}>
            <span title="Drag to reorder" draggable
              onClick={(e) => e.stopPropagation()}
              onDragStart={(e) => {
                setDragIdx(i);
                e.dataTransfer.effectAllowed = "move";
                try { e.dataTransfer.setData("text/plain", String(i)); } catch { /* some browsers */ }
              }}
              onDragEnd={() => { setDragIdx(null); setOverIdx(null); }}
              className="cursor-grab active:cursor-grabbing -ml-1 px-0.5 shrink-0"
              style={{ color: "var(--ios-gray-1)", touchAction: "none" }}>
              <GripVertical className="h-4 w-4" />
            </span>
            <ChevronRight className="h-3.5 w-3.5 opacity-50 transition-transform"
              style={{ transform: isOpen ? "rotate(90deg)" : "none" }} />
            <BlockIcon type={b.type} />
            <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
              {PALETTE.find((p) => p.type === b.type)?.label ?? b.type}
            </span>
            {!isOpen && (
              <span className="text-[11px] truncate" style={{ color: "var(--ios-gray-1)" }}>· {summarize(b)}</span>
            )}
            <div className="ml-auto flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              <IconBtn title="Move up" disabled={i === 0} onClick={() => move(i, -1)}><ChevronUp className="h-3.5 w-3.5" /></IconBtn>
              <IconBtn title="Move down" disabled={i === blocks.length - 1} onClick={() => move(i, 1)}><ChevronDown className="h-3.5 w-3.5" /></IconBtn>
              <IconBtn title="Duplicate" onClick={() => duplicate(i)}><Copy className="h-3.5 w-3.5" /></IconBtn>
              <IconBtn title="Delete" onClick={() => remove(i)} danger><Trash2 className="h-3.5 w-3.5" /></IconBtn>
            </div>
          </div>
          {/* block body */}
          {isOpen && (
          <div className="p-3">
            <BlockBody block={b} onPatch={(p) => update(i, p)} allTags={allTags} page={pageSettings} advanced={advanced} />
            {advanced && STYLEABLE.has(b.type) && (
              <details className="mt-2 rounded-md" style={{ border: "0.5px solid var(--separator)" }}>
                <summary className="cursor-pointer px-2 py-1 text-[11px] font-medium"
                  style={{ color: "var(--ios-gray-1)" }}>Style (font, color, border)</summary>
                <div className="p-2 pt-1"><StylePanel block={b} onPatch={(p) => update(i, p)} /></div>
              </details>
            )}
          </div>
          )}
        </div>
        );
      })}

      {/* add-block palette */}
      {adding ? (
        <div className="rounded-lg p-2 grid gap-1.5"
          style={{ border: "0.5px solid var(--separator)", gridTemplateColumns: "repeat(2, 1fr)" }}>
          {PALETTE.map((p) => (
            <button key={p.type} onClick={() => add(p.type)}
              className="text-left rounded-md px-2.5 py-1.5 hover:opacity-80 flex items-center gap-2"
              style={{ border: "0.5px solid var(--separator)", backgroundColor: "var(--bg-elevated)" }}>
              <BlockIcon type={p.type} />
              <span className="min-w-0">
                <div className="text-[12px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{p.label}</div>
                <div className="text-[10px] truncate" style={{ color: "var(--ios-gray-1)" }}>{p.hint}</div>
              </span>
            </button>
          ))}
          <button onClick={() => setAdding(false)}
            className="col-span-2 text-[11px] py-1" style={{ color: "var(--ios-gray-1)" }}>Cancel</button>
        </div>
      ) : (
        <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
          <Plus className="h-3.5 w-3.5" /><span className="ml-1">Add block</span>
        </Button>
      )}
    </div>
  );
}

function IconBtn({
  children, onClick, title, disabled, danger,
}: { children: React.ReactNode; onClick: () => void; title: string; disabled?: boolean; danger?: boolean }) {
  return (
    <button type="button" title={title} onClick={onClick} disabled={disabled}
      className="h-6 w-6 inline-flex items-center justify-center rounded-md disabled:opacity-30"
      style={{ color: danger ? "var(--ios-red)" : "var(--text-secondary)" }}>
      {children}
    </button>
  );
}

function BlockBody({
  block, onPatch, allTags, page, advanced,
}: { block: Block; onPatch: (p: Partial<Block>) => void; allTags: TagLite[]; page?: { size: string; orientation: string }; advanced?: boolean }) {
  const inputStyle = {
    backgroundColor: "var(--bg, #fff)",
    border: "0.5px solid var(--separator)",
  };

  if (block.type === "header") {
    return (
      <div className="space-y-2">
        <Field label="Title">
          <Select value={block.title_source ?? "report_name"} options={["report_name", "custom"]}
            labels={["Report name", "Custom text"]}
            onChange={(v) => onPatch({ title_source: v })} />
        </Field>
        {block.title_source === "custom" && (
          <Field label="Custom title">
            <Input value={block.custom_title ?? ""} onChange={(e) => onPatch({ custom_title: e.target.value })} />
          </Field>
        )}
        <label className="flex items-center gap-2 text-[12px]" style={{ color: "var(--text-primary)" }}>
          <input type="checkbox" checked={!!block.show_generated}
            onChange={(e) => onPatch({ show_generated: e.target.checked })} />
          Show generated timestamp
        </label>
        <label className="flex items-center gap-2 text-[12px]" style={{ color: "var(--text-primary)" }}>
          <input type="checkbox" checked={!!block.show_logo}
            onChange={(e) => onPatch({ show_logo: e.target.checked })} />
          Show logo (when configured)
        </label>
      </div>
    );
  }

  if (block.type === "report_style") {
    return <ReportStyleEditor block={block} onPatch={onPatch} advanced={advanced} />;
  }

  if (block.type === "page_header" || block.type === "page_footer") {
    return <HeaderFooterEditor block={block} onPatch={onPatch} />;
  }

  if (block.type === "text") {
    return (
      <Field label="Text — insert a tag value with {tag:TagName}">
        <textarea value={block.content ?? ""} onChange={(e) => onPatch({ content: e.target.value })}
          spellCheck={false} className="w-full rounded-md p-2 text-[12px]"
          style={{ ...inputStyle, minHeight: 70 }} />
      </Field>
    );
  }

  if (block.type === "tag_table") {
    return (
      <>
        <ColumnsEditor columns={(block.columns ?? []) as Column[]} onChange={(cols) => onPatch({ columns: cols })} />
        {advanced && <BandingControls block={block} onPatch={onPatch} />}
      </>
    );
  }

  if (block.type === "stream_table") {
    return (
      <>
        <StreamTableEditor block={block} onPatch={onPatch} page={page} advanced={advanced} />
        {advanced && <BandingControls block={block} onPatch={onPatch} />}
      </>
    );
  }

  if (block.type === "kpi_row") {
    return <KpiItemsEditor items={block.items ?? []} onChange={(items) => onPatch({ items })} allTags={allTags} />;
  }

  if (block.type === "chart") {
    return (
      <div className="space-y-2">
        <Field label="Chart title (optional)">
          <Input value={block.title ?? ""} onChange={(e) => onPatch({ title: e.target.value })} />
        </Field>
        <p className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
          Chart rendering is not wired yet — a placeholder appears in the output for now.
        </p>
      </div>
    );
  }

  if (block.type === "spacer") {
    return (
      <Field label="Height (mm)">
        <Input type="number" value={String(block.height_mm ?? 6)}
          onChange={(e) => onPatch({ height_mm: Number(e.target.value) || 0 })} />
      </Field>
    );
  }

  if (block.type === "page_break") {
    return <p className="text-[12px]" style={{ color: "var(--ios-gray-1)" }}>Forces a new page at this point.</p>;
  }

  if (block.type === "raw") {
    return (
      <Field label="Raw HTML / Jinja2 (advanced)">
        <textarea value={block.html ?? ""} onChange={(e) => onPatch({ html: e.target.value })}
          spellCheck={false} className="w-full rounded-md p-2 font-mono text-[12px]"
          style={{ ...inputStyle, minHeight: 90 }} />
      </Field>
    );
  }

  return <p className="text-[12px]" style={{ color: "var(--ios-gray-1)" }}>Unknown block type.</p>;
}

const FONTS = ["Segoe UI", "Arial", "Calibri", "Times New Roman", "Verdana", "Tahoma", "Roboto", "Noto Sans", "Courier New"];
const STYLEABLE = new Set(["text", "header", "stream_table", "kpi_row"]);

function ColorField({ label, value, onChange }: { label: string; value?: string; onChange: (v: string) => void }) {
  const v = value ?? "";
  const hex = /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#000000";
  return (
    <Field label={label}>
      <div className="flex items-center gap-2">
        <input type="color" value={hex} onChange={(e) => onChange(e.target.value)}
          style={{ width: 30, height: 26, border: "0.5px solid var(--separator)", borderRadius: 4, padding: 0, background: "transparent" }} />
        <Input value={v} placeholder="#RRGGBB (blank = inherit)" onChange={(e) => onChange(e.target.value)} />
        {v && <button type="button" onClick={() => onChange("")} className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>clear</button>}
      </div>
    </Field>
  );
}

export function ReportStyleEditor({ block, onPatch, scope = "report", advanced = true }:
  { block: Block; onPatch: (p: Partial<Block>) => void; scope?: "global" | "report"; advanced?: boolean }) {
  const th = block.theme ?? {};
  const tm = block.time ?? {};
  const isGlobal = scope === "global";
  const inheritPh = isGlobal ? "same as body" : "inherit";
  const setTheme = (k: string, v: any) => onPatch({ theme: { ...th, [k]: v } });
  const setTime = (k: string, v: any) => onPatch({ time: { ...tm, [k]: v } });
  return (
    <div className="space-y-2">
      {isGlobal && (
        <p className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
          Page size &amp; orientation are set per report on its <b>Settings</b> tab — not here.
        </p>
      )}
      <div className="text-[11px] font-semibold pt-1" style={{ color: "var(--ios-gray-1)" }}>TIME</div>
      <Field label="Time basis (display; storage stays UTC)">
        <Select value={tm.basis ?? "system"} options={["system", "utc"]}
          labels={["System (Asia/Kolkata)", "UTC"]} onChange={(v) => setTime("basis", v)} />
      </Field>
      <Field label="Time format (strftime)">
        <Input value={tm.format ?? ""} placeholder="%d-%b-%Y %H:%M" onChange={(e) => setTime("format", e.target.value)} />
      </Field>
      <label className="flex items-center gap-2 text-[12px]" style={{ color: "var(--text-primary)" }}>
        <input type="checkbox" checked={!!tm.show_suffix} onChange={(e) => setTime("show_suffix", e.target.checked)} />
        Show timezone suffix (e.g. IST / UTC)
      </label>
      <div className="text-[11px] font-semibold pt-1" style={{ color: "var(--ios-gray-1)" }}>THEME</div>
      <Field label="Primary font">
        <Select value={th.font_family ?? "Segoe UI"} options={FONTS} labels={FONTS} onChange={(v) => setTheme("font_family", v)} />
      </Field>
      <Field label="Base font size (pt)">
        <Input type="number" value={String(th.font_size_pt ?? 10)} onChange={(e) => setTheme("font_size_pt", Number(e.target.value) || 10)} />
      </Field>
      <ColorField label="Body text" value={th.text_color} onChange={(v) => setTheme("text_color", v)} />
      <ColorField label="Heading color" value={th.heading_color} onChange={(v) => setTheme("heading_color", v)} />
      {advanced && (<>
      <div className="text-[11px] font-semibold pt-1" style={{ color: "var(--ios-gray-1)" }}>SECTION HEADINGS</div>
      <ColorField label="Section text color" value={th.section_color} onChange={(v) => setTheme("section_color", v)} />
      <ColorField label="Section background (band)" value={th.section_bg} onChange={(v) => setTheme("section_bg", v)} />
      <div className="grid gap-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <Field label="Section size (pt)">
          <Input type="number" value={th.section_size_pt ? String(th.section_size_pt) : ""} placeholder={inheritPh}
            onChange={(e) => setTheme("section_size_pt", e.target.value ? Number(e.target.value) : undefined)} />
        </Field>
        <label className="flex items-center gap-2 text-[12px] self-end" style={{ color: "var(--text-primary)" }}>
          <input type="checkbox" checked={!!th.section_caps} onChange={(e) => setTheme("section_caps", e.target.checked || undefined)} /> UPPERCASE
        </label>
      </div>
      <ColorField label="Table header background" value={th.table_header_bg} onChange={(v) => setTheme("table_header_bg", v)} />
      <ColorField label="Table header text" value={th.table_header_fg} onChange={(v) => setTheme("table_header_fg", v)} />
      <ColorField label="Alternate row" value={th.alt_row} onChange={(v) => setTheme("alt_row", v)} />
      <div className="text-[11px] font-semibold pt-1" style={{ color: "var(--ios-gray-1)" }}>DATA QUALITY &amp; LINEAGE</div>
      <label className="flex items-center gap-2 text-[12px]" style={{ color: "var(--text-primary)" }}>
        <input type="checkbox"
          checked={(block.quality?.enabled ?? true) !== false}
          onChange={(e) => onPatch({ quality: { ...(block.quality ?? {}), enabled: e.target.checked } })} />
        Show data-quality markers (color + symbol on bad / uncertain / stale / missing)
      </label>
      <label className="flex items-center gap-2 text-[12px]" style={{ color: "var(--text-primary)" }}>
        <input type="checkbox"
          checked={(block.lineage?.enabled ?? true) !== false}
          onChange={(e) => onPatch({ lineage: { ...(block.lineage ?? {}), enabled: e.target.checked } })} />
        Show provenance tooltips (hover a value for source, quality, time, origin)
      </label>
      </>)}
      <p className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
        {isGlobal
          ? "These defaults apply to every report. A blank field uses the built-in base value (e.g. section size = body size)."
          : "Overrides the global default for this report only. Leave a field blank to inherit the global default; delete this block to follow the global default entirely."}
      </p>
    </div>
  );
}

function BandingControls({ block, onPatch }: { block: Block; onPatch: (p: Partial<Block>) => void }) {
  return (
    <div className="mt-3 rounded-md p-2" style={{ border: "0.5px solid var(--separator)" }}>
      <div className="text-[11px] font-semibold mb-1" style={{ color: "var(--ios-gray-1)" }}>BANDING</div>
      <div className="grid gap-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <label className="flex items-center gap-2 text-[12px] self-end" style={{ color: "var(--text-primary)" }}>
          <input type="checkbox" checked={!!block.band_rows} onChange={(e) => onPatch({ band_rows: e.target.checked })} /> Banded rows
        </label>
        {block.band_rows
          ? <ColorField label="Row band color" value={block.band_row_color} onChange={(v) => onPatch({ band_row_color: v || undefined })} />
          : <div />}
        <label className="flex items-center gap-2 text-[12px] self-end" style={{ color: "var(--text-primary)" }}>
          <input type="checkbox" checked={!!block.band_cols} onChange={(e) => onPatch({ band_cols: e.target.checked })} /> Banded columns
        </label>
        {block.band_cols
          ? <ColorField label="Column band color" value={block.band_col_color} onChange={(v) => onPatch({ band_col_color: v || undefined })} />
          : <div />}
      </div>
      <p className="text-[11px] mt-1" style={{ color: "var(--ios-gray-1)" }}>
        Shades alternate data rows / data columns for readability (PDF + HTML).
      </p>
    </div>
  );
}

function StylePanel({ block, onPatch }: { block: Block; onPatch: (p: Partial<Block>) => void }) {
  const st = block.style ?? {};
  const font = st.font ?? {}, text = st.text ?? {}, border = st.border ?? {};
  const setFont = (k: string, v: any) => onPatch({ style: { ...st, font: { ...font, [k]: v } } });
  const setText = (k: string, v: any) => onPatch({ style: { ...st, text: { ...text, [k]: v } } });
  const setBorder = (k: string, v: any) => onPatch({ style: { ...st, border: { ...border, [k]: v } } });
  const setBg = (v: string) => onPatch({ style: { ...st, background: v ? { type: "solid", color: v } : undefined } });
  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
      <Field label="Font"><Select value={font.family ?? ""} options={["", ...FONTS]} labels={["Inherit", ...FONTS]}
        onChange={(v) => setFont("family", v || undefined)} /></Field>
      <Field label="Size (pt)"><Input type="number" value={font.size_pt ? String(font.size_pt) : ""} placeholder="inherit"
        onChange={(e) => setFont("size_pt", e.target.value ? Number(e.target.value) : undefined)} /></Field>
      <Field label="Weight"><Select value={font.weight ?? ""} options={["", "regular", "bold"]} labels={["Inherit", "Regular", "Bold"]}
        onChange={(v) => setFont("weight", v || undefined)} /></Field>
      <Field label="Case"><Select value={font.case ?? "as_typed"} options={["as_typed", "uppercase", "lowercase", "capitalize"]}
        labels={["As typed", "UPPER", "lower", "Capitalize"]} onChange={(v) => setFont("case", v)} /></Field>
      <Field label="Align"><Select value={text.horizontal_align ?? ""} options={["", "left", "center", "right", "justify"]}
        labels={["Inherit", "Left", "Center", "Right", "Justify"]} onChange={(v) => setText("horizontal_align", v || undefined)} /></Field>
      <label className="flex items-center gap-2 text-[12px] self-end" style={{ color: "var(--text-primary)" }}>
        <input type="checkbox" checked={!!font.italic} onChange={(e) => setFont("italic", e.target.checked || undefined)} /> Italic
      </label>
      <ColorField label="Text color" value={text.color} onChange={(v) => setText("color", v || undefined)} />
      <ColorField label="Background" value={st.background?.color} onChange={setBg} />
      <label className="flex items-center gap-2 text-[12px] self-end" style={{ color: "var(--text-primary)" }}>
        <input type="checkbox" checked={!!border.enabled} onChange={(e) => setBorder("enabled", e.target.checked)} /> Border
      </label>
      {border.enabled && <ColorField label="Border color" value={border.color} onChange={(v) => setBorder("color", v || undefined)} />}
    </div>
  );
}

const HF_TOKENS = ["{page_of}", "{page}", "{pages}", "{report_title}", "{generated_at}", "{timezone}"];

function HeaderFooterEditor({ block, onPatch }: { block: Block; onPatch: (p: Partial<Block>) => void }) {
  const [target, setTarget] = useState<"left" | "center" | "right">("left");
  const isHeader = block.type === "page_header";
  const slots: ("left" | "center" | "right")[] = ["left", "center", "right"];
  const cap = (s: string) => s[0].toUpperCase() + s.slice(1);
  return (
    <div className="space-y-2">
      {slots.map((s) => (
        <Field key={s} label={`${cap(s)} ${isHeader ? "(header)" : "(footer)"}`}>
          <Input value={block[s] ?? ""} placeholder={isHeader ? "" : s === "center" ? "e.g. {page_of}" : ""}
            onChange={(e) => onPatch({ [s]: e.target.value })} />
        </Field>
      ))}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <span className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>Insert token into</span>
        <Select value={target} options={["left", "center", "right"]} labels={["Left", "Center", "Right"]}
          onChange={(v) => setTarget(v as "left" | "center" | "right")} />
      </div>
      <div className="flex flex-wrap gap-1">
        {HF_TOKENS.map((t) => (
          <button key={t} type="button"
            onClick={() => onPatch({ [target]: `${block[target] ?? ""}${t}` })}
            className="rounded px-1.5 py-0.5 text-[11px]"
            style={{ border: "0.5px solid var(--separator)", color: "var(--text-primary)" }}>
            {t}
          </button>
        ))}
      </div>
      <p className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
        Repeats on every PDF page. <code>{"{page}"}</code>/<code>{"{pages}"}</code>/<code>{"{page_of}"}</code> are
        live page numbers (PDF only); <code>{"{report_title}"}</code> <code>{"{generated_at}"}</code>{" "}
        <code>{"{timezone}"}</code> fill from the report. Plain text is allowed.
      </p>
    </div>
  );
}

function ColumnsEditor({ columns, onChange }: { columns: Column[]; onChange: (c: Column[]) => void }) {
  const update = (i: number, patch: Partial<Column>) =>
    onChange(columns.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const remove = (i: number) => onChange(columns.filter((_, j) => j !== i));
  const addData = () => onChange([...columns, { key: "value", label: "Value", kind: "data" }]);
  const addFormula = () => onChange([...columns, { key: `calc_${columns.length + 1}`, label: "Calc", kind: "formula", formula: "" }]);

  return (
    <div className="space-y-2">
      <div className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>
        One row per bound tag. Data columns read a tag field; formula columns compute from
        other columns (e.g. <code>{"{value} * 1.8 + 32"}</code>) or table totals (e.g. <code>{"{sum:value}"}</code>).
      </div>
      {columns.map((c, i) => (
        <div key={i} className="rounded-md p-2 grid gap-2"
          style={{ border: "0.5px solid var(--separator)", gridTemplateColumns: "1fr 1fr auto" }}>
          <Field label="Label">
            <Input value={c.label ?? ""} onChange={(e) => update(i, { label: e.target.value })} />
          </Field>
          {c.kind === "data" ? (
            <Field label="Tag field">
              <Select value={c.key} options={DATA_KEYS} labels={DATA_KEY_LABELS}
                onChange={(v) => update(i, { key: v })} />
            </Field>
          ) : (
            <Field label="Column id">
              <Input value={c.key} onChange={(e) => update(i, { key: e.target.value })} />
            </Field>
          )}
          <div className="flex items-end pb-1">
            <IconBtn title="Remove column" onClick={() => remove(i)} danger><Trash2 className="h-3.5 w-3.5" /></IconBtn>
          </div>
          {c.kind === "formula" && (
            <div className="col-span-3">
              <Field label="Formula">
                <Input value={c.formula ?? ""} onChange={(e) => update(i, { formula: e.target.value })} />
              </Field>
            </div>
          )}
        </div>
      ))}
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={addData}><Plus className="h-3.5 w-3.5" /><span className="ml-1">Data column</span></Button>
        <Button variant="outline" size="sm" onClick={addFormula}><Plus className="h-3.5 w-3.5" /><span className="ml-1">Formula column</span></Button>
      </div>
    </div>
  );
}

function KpiItemsEditor({
  items, onChange, allTags,
}: { items: any[]; onChange: (i: any[]) => void; allTags: TagLite[] }) {
  const update = (i: number, patch: any) => onChange(items.map((it, j) => (j === i ? { ...it, ...patch } : it)));
  const remove = (i: number) => onChange(items.filter((_, j) => j !== i));
  const add = () => onChange([...items, { tag_id: allTags[0]?.id ?? 0, label: "", unit: "", decimals: undefined }]);
  const tagOptions = allTags.map((t) => String(t.id));
  const tagLabels = allTags.map((t) => t.name);

  return (
    <div className="space-y-2">
      {items.length === 0 && (
        <div className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>No tiles yet.</div>
      )}
      {items.map((it, i) => (
        <div key={i} className="rounded-md p-2 grid gap-2"
          style={{ border: "0.5px solid var(--separator)", gridTemplateColumns: "1.3fr 1.3fr 0.9fr 0.6fr auto" }}>
          <Field label="Tag">
            <Select value={String(it.tag_id ?? "")} options={tagOptions} labels={tagLabels}
              onChange={(v) => update(i, { tag_id: Number(v) })} />
          </Field>
          <Field label="Label">
            <Input value={it.label ?? ""} onChange={(e) => update(i, { label: e.target.value })} />
          </Field>
          <Field label="Unit">
            <Input value={it.unit ?? ""} onChange={(e) => update(i, { unit: e.target.value })} />
          </Field>
          <Field label="Dec">
            <Input type="number" value={it.decimals != null ? String(it.decimals) : ""} placeholder="auto"
              onChange={(e) => update(i, { decimals: e.target.value !== "" ? Number(e.target.value) : undefined })} />
          </Field>
          <div className="flex items-end pb-1">
            <IconBtn title="Remove tile" onClick={() => remove(i)} danger><Trash2 className="h-3.5 w-3.5" /></IconBtn>
          </div>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={add} disabled={allTags.length === 0}>
        <Plus className="h-3.5 w-3.5" /><span className="ml-1">Add tile</span>
      </Button>
    </div>
  );
}
