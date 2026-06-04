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
  Plus, Trash2, ChevronUp, ChevronDown, Copy, GripVertical,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, Select, type TagLite } from "@/pages/triggers-shared";

export type Block = { id: string; type: string; [k: string]: any };
type Column = { key: string; label: string; kind: "data" | "formula"; formula?: string; fmt?: string };

// Block types offered in the palette (columns/chart kept minimal for now —
// chart rendering is pending TC-3; nested columns is a later polish).
const PALETTE: { type: string; label: string; hint: string }[] = [
  { type: "header", label: "Header", hint: "Title + generated timestamp" },
  { type: "text", label: "Text", hint: "Paragraph; use {tag:Name} inline" },
  { type: "tag_table", label: "Tag table", hint: "One row per bound tag + calc columns" },
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
    case "text": return { id, type, content: "" };
    case "tag_table": return { id, type, columns: [
      { key: "name", label: "Tag", kind: "data" },
      { key: "display", label: "Value", kind: "data" },
      { key: "unit", label: "Unit", kind: "data" },
    ] as Column[] };
    case "kpi_row": return { id, type, items: [] };
    case "chart": return { id, type, title: "" };
    case "spacer": return { id, type, height_mm: 6 };
    case "page_break": return { id, type };
    case "raw": return { id, type, html: "" };
    default: return { id, type };
  }
}

export function ReportBlocksEditor({
  value, onChange, allTags,
}: { value: Block[]; onChange: (b: Block[]) => void; allTags: TagLite[] }) {
  const blocks = Array.isArray(value) ? value : [];
  const [adding, setAdding] = useState(false);

  const add = (type: string) => { onChange([...blocks, defaults(type)]); setAdding(false); };
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

  return (
    <div className="space-y-2">
      {blocks.length === 0 && (
        <div className="rounded-lg p-4 text-center text-[12px]"
          style={{ border: "1px dashed var(--separator)", color: "var(--ios-gray-1)" }}>
          No blocks yet. Add a Header, then a Tag table or KPI row.
        </div>
      )}

      {blocks.map((b, i) => (
        <div key={b.id} className="rounded-lg overflow-hidden"
          style={{ border: "0.5px solid var(--separator)", backgroundColor: "var(--bg-elevated)" }}>
          {/* block header bar */}
          <div className="flex items-center gap-2 px-3 py-2"
            style={{ borderBottom: "0.5px solid var(--separator)", backgroundColor: "var(--bg-grouped)" }}>
            <GripVertical className="h-3.5 w-3.5 opacity-40" />
            <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
              {PALETTE.find((p) => p.type === b.type)?.label ?? b.type}
            </span>
            <code className="text-[10px] opacity-50">{b.id}</code>
            <div className="ml-auto flex items-center gap-1">
              <IconBtn title="Move up" disabled={i === 0} onClick={() => move(i, -1)}><ChevronUp className="h-3.5 w-3.5" /></IconBtn>
              <IconBtn title="Move down" disabled={i === blocks.length - 1} onClick={() => move(i, 1)}><ChevronDown className="h-3.5 w-3.5" /></IconBtn>
              <IconBtn title="Duplicate" onClick={() => duplicate(i)}><Copy className="h-3.5 w-3.5" /></IconBtn>
              <IconBtn title="Delete" onClick={() => remove(i)} danger><Trash2 className="h-3.5 w-3.5" /></IconBtn>
            </div>
          </div>
          {/* block body */}
          <div className="p-3">
            <BlockBody block={b} onPatch={(p) => update(i, p)} allTags={allTags} />
          </div>
        </div>
      ))}

      {/* add-block palette */}
      {adding ? (
        <div className="rounded-lg p-2 grid gap-1.5"
          style={{ border: "0.5px solid var(--separator)", gridTemplateColumns: "repeat(2, 1fr)" }}>
          {PALETTE.map((p) => (
            <button key={p.type} onClick={() => add(p.type)}
              className="text-left rounded-md px-2.5 py-1.5 hover:opacity-80"
              style={{ border: "0.5px solid var(--separator)", backgroundColor: "var(--bg-elevated)" }}>
              <div className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>{p.label}</div>
              <div className="text-[10px]" style={{ color: "var(--ios-gray-1)" }}>{p.hint}</div>
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
  block, onPatch, allTags,
}: { block: Block; onPatch: (p: Partial<Block>) => void; allTags: TagLite[] }) {
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
    return <ColumnsEditor columns={(block.columns ?? []) as Column[]} onChange={(cols) => onPatch({ columns: cols })} />;
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
  const add = () => onChange([...items, { tag_id: allTags[0]?.id ?? 0, label: "" }]);
  const tagOptions = allTags.map((t) => String(t.id));
  const tagLabels = allTags.map((t) => t.name);

  return (
    <div className="space-y-2">
      {items.length === 0 && (
        <div className="text-[11px]" style={{ color: "var(--ios-gray-1)" }}>No tiles yet.</div>
      )}
      {items.map((it, i) => (
        <div key={i} className="rounded-md p-2 grid gap-2"
          style={{ border: "0.5px solid var(--separator)", gridTemplateColumns: "1fr 1fr auto" }}>
          <Field label="Tag">
            <Select value={String(it.tag_id ?? "")} options={tagOptions} labels={tagLabels}
              onChange={(v) => update(i, { tag_id: Number(v) })} />
          </Field>
          <Field label="Label">
            <Input value={it.label ?? ""} onChange={(e) => update(i, { label: e.target.value })} />
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
