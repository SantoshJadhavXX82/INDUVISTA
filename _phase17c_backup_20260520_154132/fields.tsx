/**
 * Phase 16.0b - Schema-driven form field components.
 *
 * All 8 field types live here. Each component takes the same props
 * (the entire blockConfig plus an onChange that produces a new one),
 * reads its slice, writes its slice. TagOrConstantField is the
 * special one: it manages mutual exclusion between block_config[key]
 * (a tag id) and block_config.value (a numeric constant). Both
 * keys are MUTUALLY EXCLUSIVE in the saved config; the backend's
 * validate_config enforces this on save.
 *
 * Styling matches the dense, low-chrome look of the existing admin
 * pages (CalcDefinitionsAdmin, etc): small text, tight padding,
 * shadcn primitives where they exist.
 */
import { useMemo, useState, useEffect } from "react";
import { Plus, X, AlertTriangle } from "lucide-react";

import type {
  FieldDef, BlockConfigDraft, TagFilter, ModeOption,
} from "@/types/calcBlockSchemas";
import { useTagsList, type TagListItem } from "@/lib/useTagsList";


// ---------------------------------------------------------------------------
// Shared FieldProps interface
// ---------------------------------------------------------------------------

export interface FieldProps {
  field: FieldDef;
  blockConfig: BlockConfigDraft;
  onChange: (next: BlockConfigDraft) => void;
}

const inputCls =
  "h-7 text-xs bg-card border border-border rounded px-2 w-full " +
  "focus:outline-none focus:ring-1 focus:ring-primary";

const labelCls =
  "text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5 block";


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function filterTags(tags: TagListItem[], filter?: TagFilter): TagListItem[] {
  if (!filter) return tags;
  return tags.filter((t) => {
    if (filter.data_type && !filter.data_type.includes(t.data_type)) return false;
    return true;
  });
}

function fieldLabel(field: FieldDef) {
  return (
    <span className={labelCls}>
      {field.label}
      {field.required && <span className="text-destructive">*</span>}
    </span>
  );
}

function fieldHelp(field: FieldDef) {
  if (!field.help) return null;
  return <p className="text-[10px] text-muted-foreground mt-0.5">{field.help}</p>;
}


// ===========================================================================
// TagRefField - single tag picker
// ===========================================================================

export function TagRefField({ field, blockConfig, onChange }: FieldProps) {
  const tags = useTagsList();
  const currentTagId = (blockConfig[field.key] as number | undefined) ?? "";

  const available = useMemo(
    () => filterTags(tags.data ?? [], field.filter),
    [tags.data, field.filter],
  );

  return (
    <div>
      {fieldLabel(field)}
      <select
        className={inputCls}
        value={currentTagId === "" ? "" : String(currentTagId)}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") {
            const { [field.key]: _removed, ...rest } = blockConfig;
            onChange(rest);
          } else {
            onChange({ ...blockConfig, [field.key]: Number(v) });
          }
        }}
      >
        <option value="">— select tag —</option>
        {available.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name} ({t.data_type}, #{t.id})
          </option>
        ))}
      </select>
      {tags.isLoading && <p className="text-[10px] text-muted-foreground">Loading tags…</p>}
      {fieldHelp(field)}
    </div>
  );
}


// ===========================================================================
// TagRefListField - ordered multi-tag picker
// ===========================================================================

export function TagRefListField({ field, blockConfig, onChange }: FieldProps) {
  const tags = useTagsList();
  const tagIds = (blockConfig[field.key] as number[] | undefined) ?? [];
  const minItems = field.minItems ?? 1;
  const maxItems = field.maxItems ?? 100;

  const available = useMemo(
    () => filterTags(tags.data ?? [], field.filter),
    [tags.data, field.filter],
  );

  const setIds = (next: number[]) => {
    onChange({ ...blockConfig, [field.key]: next });
  };

  const addRow = () => setIds([...tagIds, available[0]?.id ?? 0]);
  const removeRow = (i: number) => setIds(tagIds.filter((_, idx) => idx !== i));
  const updateRow = (i: number, newId: number) => {
    const next = [...tagIds];
    next[i] = newId;
    setIds(next);
  };

  const canRemove = tagIds.length > minItems;
  const canAdd = tagIds.length < maxItems;

  return (
    <div>
      {fieldLabel(field)}
      <div className="space-y-1">
        {tagIds.length === 0 && (
          <p className="text-[11px] text-muted-foreground italic">
            (no tags selected — add at least {minItems})
          </p>
        )}
        {tagIds.map((tid, i) => (
          <div key={i} className="flex items-center gap-1">
            <span className="text-[10px] text-muted-foreground w-5 text-right">{i + 1}.</span>
            <select
              className={inputCls}
              value={String(tid)}
              onChange={(e) => updateRow(i, Number(e.target.value))}
            >
              <option value="0">— select tag —</option>
              {available.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.data_type}, #{t.id})
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => removeRow(i)}
              disabled={!canRemove}
              className="h-7 w-7 inline-flex items-center justify-center rounded
                         text-muted-foreground hover:text-destructive
                         hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed"
              title="Remove"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={addRow}
          disabled={!canAdd}
          className="text-[11px] inline-flex items-center gap-1 px-2 py-1
                     rounded border border-dashed border-border
                     hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <Plus className="h-3 w-3" />
          Add tag
        </button>
        <span className="text-[10px] text-muted-foreground ml-2">
          {tagIds.length} / max {maxItems}
        </span>
      </div>
      {fieldHelp(field)}
    </div>
  );
}


// ===========================================================================
// TagOrConstantField - mutex: either {field.key: tag_id} or {value: number}
// ===========================================================================

export function TagOrConstantField({ field, blockConfig, onChange }: FieldProps) {
  const hasConst = "value" in blockConfig && !(field.key in blockConfig);
  const mode: "tag" | "constant" = hasConst ? "constant" : "tag";

  const switchToTag = () => {
    const { value: _v, ...rest } = blockConfig;
    onChange(rest);  // tag id will be set by the picker below
  };

  const switchToConstant = () => {
    const { [field.key]: _removed, ...rest } = blockConfig;
    onChange({ ...rest, value: 0 });
  };

  return (
    <div>
      {fieldLabel(field)}
      <div className="flex items-center gap-3 mb-1 text-[11px]">
        <label className="flex items-center gap-1 cursor-pointer">
          <input
            type="radio"
            checked={mode === "tag"}
            onChange={switchToTag}
            className="cursor-pointer"
          />
          From tag
        </label>
        <label className="flex items-center gap-1 cursor-pointer">
          <input
            type="radio"
            checked={mode === "constant"}
            onChange={switchToConstant}
            className="cursor-pointer"
          />
          Constant value
        </label>
      </div>
      {mode === "tag" ? (
        <TagRefField
          field={{ ...field, label: "", help: undefined }}
          blockConfig={blockConfig}
          onChange={onChange}
        />
      ) : (
        <input
          className={inputCls}
          type="number"
          step="any"
          value={(blockConfig.value as number | undefined) ?? 0}
          onChange={(e) => {
            const num = e.target.value === "" ? 0 : Number(e.target.value);
            onChange({ ...blockConfig, value: num });
          }}
          placeholder="numeric constant"
        />
      )}
      {fieldHelp(field)}
    </div>
  );
}


// ===========================================================================
// IntegerField / NumberField
// ===========================================================================

export function IntegerField({ field, blockConfig, onChange }: FieldProps) {
  const current = blockConfig[field.key];
  const value = current === undefined || current === null ? "" : String(current);

  return (
    <div>
      {fieldLabel(field)}
      <input
        type="number"
        step="1"
        min={field.min}
        max={field.max}
        className={inputCls}
        value={value}
        placeholder={field.default !== undefined ? String(field.default) : ""}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") {
            const { [field.key]: _r, ...rest } = blockConfig;
            onChange(rest);
          } else {
            onChange({ ...blockConfig, [field.key]: Math.trunc(Number(v)) });
          }
        }}
      />
      {fieldHelp(field)}
    </div>
  );
}

export function NumberField({ field, blockConfig, onChange }: FieldProps) {
  const current = blockConfig[field.key];
  const value = current === undefined || current === null ? "" : String(current);

  return (
    <div>
      {fieldLabel(field)}
      <input
        type="number"
        step="any"
        min={field.min}
        max={field.max}
        className={inputCls}
        value={value}
        placeholder={field.default !== undefined ? String(field.default) : ""}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") {
            const { [field.key]: _r, ...rest } = blockConfig;
            onChange(rest);
          } else {
            onChange({ ...blockConfig, [field.key]: Number(v) });
          }
        }}
      />
      {fieldHelp(field)}
    </div>
  );
}


// ===========================================================================
// NumberListField (used by WEIGHTED_AVG.weights)
// ===========================================================================

export function NumberListField({ field, blockConfig, onChange }: FieldProps) {
  const values = (blockConfig[field.key] as number[] | undefined) ?? [];

  const setValues = (next: number[]) => {
    onChange({ ...blockConfig, [field.key]: next });
  };

  return (
    <div>
      {fieldLabel(field)}
      <div className="space-y-1">
        {values.length === 0 && (
          <p className="text-[11px] text-muted-foreground italic">(no values)</p>
        )}
        {values.map((v, i) => (
          <div key={i} className="flex items-center gap-1">
            <span className="text-[10px] text-muted-foreground w-5 text-right">{i + 1}.</span>
            <input
              type="number"
              step="any"
              className={inputCls}
              value={v}
              onChange={(e) => {
                const next = [...values];
                next[i] = Number(e.target.value);
                setValues(next);
              }}
            />
            <button
              type="button"
              onClick={() => setValues(values.filter((_, idx) => idx !== i))}
              className="h-7 w-7 inline-flex items-center justify-center rounded
                         text-muted-foreground hover:text-destructive hover:bg-secondary"
              title="Remove"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => setValues([...values, 1])}
          className="text-[11px] inline-flex items-center gap-1 px-2 py-1
                     rounded border border-dashed border-border hover:bg-secondary"
        >
          <Plus className="h-3 w-3" />
          Add value
        </button>
      </div>
      {fieldHelp(field)}
    </div>
  );
}


// ===========================================================================
// BooleanField / EnumField (not used by current blocks but supported)
// ===========================================================================

export function BooleanField({ field, blockConfig, onChange }: FieldProps) {
  const current = !!blockConfig[field.key];

  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={current}
        onChange={(e) => onChange({ ...blockConfig, [field.key]: e.target.checked })}
      />
      <span className="text-xs">{field.label}</span>
      {field.required && <span className="text-destructive">*</span>}
      {field.help && <span className="text-[10px] text-muted-foreground">{field.help}</span>}
    </label>
  );
}

export function EnumField({ field, blockConfig, onChange }: FieldProps) {
  const current = blockConfig[field.key];
  const opts = (field.options as { value: unknown; label: string }[] | undefined) ?? [];

  return (
    <div>
      {fieldLabel(field)}
      <select
        className={inputCls}
        value={current === undefined ? "" : String(current)}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") {
            const { [field.key]: _r, ...rest } = blockConfig;
            onChange(rest);
          } else {
            const opt = opts.find((o) => String(o.value) === v);
            onChange({ ...blockConfig, [field.key]: opt ? opt.value : v });
          }
        }}
      >
        <option value="">— select —</option>
        {opts.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>{o.label}</option>
        ))}
      </select>
      {fieldHelp(field)}
    </div>
  );
}


// ===========================================================================
// ModeSelectField - discriminated form: radio picks a mode, sub-fields
// for that mode render below. Used by ADD and MUL to expose binary +
// N-ary modes in a single form.
// ===========================================================================

export function ModeSelectField({ field, blockConfig, onChange }: FieldProps) {
  const options = (field.options as ModeOption[] | undefined) ?? [];

  // Detect active mode from blockConfig - the mode whose sub-fields
  // have any key present wins. Fall back to schema default, then first.
  const detectMode = (): string => {
    for (const opt of options) {
      const keys = opt.fields.map((f) => f.key);
      const altKeys = opt.fields.some((f) => f.type === "tag_or_constant")
        ? ["value"]
        : [];
      if ([...keys, ...altKeys].some((k) => k in blockConfig)) return opt.value;
    }
    return (field.default as string | undefined) ?? options[0]?.value ?? "";
  };

  const [mode, setMode] = useState<string>(detectMode);

  // If parent resets blockConfig (e.g. block type changes), re-detect.
  useEffect(() => {
    const detected = detectMode();
    if (detected !== mode) setMode(detected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(blockConfig)]);

  const switchMode = (newMode: string) => {
    if (newMode === mode) return;
    // Clear keys belonging to OTHER modes so saved config is clean
    // and backend's validate_config doesn't reject a mixed shape.
    const newConfig: BlockConfigDraft = { ...blockConfig };
    for (const opt of options) {
      if (opt.value === newMode) continue;
      for (const f of opt.fields) {
        delete newConfig[f.key];
        if (f.type === "tag_or_constant") {
          delete newConfig.value;
        }
      }
    }
    onChange(newConfig);
    setMode(newMode);
  };

  const activeMode = options.find((o) => o.value === mode);

  return (
    <div>
      {fieldLabel(field)}
      <div className="flex flex-col gap-1 mb-2 text-[11px]">
        {options.map((opt) => (
          <label key={opt.value} className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              checked={mode === opt.value}
              onChange={() => switchMode(opt.value)}
              className="cursor-pointer"
            />
            <span>{opt.label}</span>
          </label>
        ))}
      </div>
      {activeMode && (
        <div className="ml-4 pl-3 border-l-2 border-border space-y-2">
          {activeMode.fields.map((f) => (
            <FieldRenderer
              key={f.key}
              field={f}
              blockConfig={blockConfig}
              onChange={onChange}
            />
          ))}
        </div>
      )}
      {fieldHelp(field)}
    </div>
  );
}


// ===========================================================================
// FieldRenderer dispatcher
// ===========================================================================

// ===========================================================================
// TagOrConstantListField - a list where each item is either a tag
// reference or a numeric constant. Used by ADD/MUL N-ary mode.
//
// Backend shape:  [{tag: <id>}, {value: <num>}, {tag: <id>}, ...]
//
// UI: each row shows either a tag-picker dropdown OR a number input
// depending on the item's type. Two add-buttons at the bottom:
// "+ Add tag" and "+ Add constant".
// ===========================================================================

type TocItem = { tag: number } | { value: number };

function isTagItem(item: TocItem): item is { tag: number } {
  return "tag" in item;
}

export function TagOrConstantListField({ field, blockConfig, onChange }: FieldProps) {
  const tagsQuery = useTagsList();
  const items = (blockConfig[field.key] as TocItem[] | undefined) ?? [];

  const minItems = field.minItems ?? 1;
  const maxItems = field.maxItems ?? 100;

  const available = useMemo(
    () => filterTags(tagsQuery.data ?? [], field.filter),
    [tagsQuery.data, field.filter]
  );

  // Tag IDs currently picked, for disabling them in other dropdowns
  // (the backend rejects duplicates).
  const pickedTagIds = items.filter(isTagItem).map((i) => i.tag);

  function setItems(next: TocItem[]) {
    onChange({ ...blockConfig, [field.key]: next });
  }

  function addTagRow() {
    setItems([...items, { tag: 0 }]);
  }

  function addConstRow() {
    setItems([...items, { value: 0 }]);
  }

  function removeRow(idx: number) {
    setItems(items.filter((_, i) => i !== idx));
  }

  function updateRow(idx: number, next: TocItem) {
    setItems(items.map((it, i) => (i === idx ? next : it)));
  }

  return (
    <div>
      {fieldLabel(field)}

      {items.length === 0 ? (
        <div className="text-[11px] text-muted-foreground italic px-2 py-1.5
                        border border-dashed border-border rounded">
          No inputs yet. Click "+ Add tag" or "+ Add constant" below.
        </div>
      ) : (
        <div className="space-y-1.5">
          {items.map((item, idx) => (
            <div key={idx} className="flex items-center gap-1.5">
              <span className="text-[10px] text-muted-foreground w-4 text-right">
                {idx + 1}.
              </span>

              {isTagItem(item) ? (
                <>
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground w-9">
                    Tag:
                  </span>
                  <select
                    className="h-7 text-xs bg-card border border-border rounded px-2 flex-1"
                    value={item.tag === 0 ? "" : String(item.tag)}
                    onChange={(e) => {
                      const v = e.target.value;
                      updateRow(idx, { tag: v === "" ? 0 : Number(v) });
                    }}
                  >
                    <option value="">— select tag —</option>
                    {available.map((t) => (
                      <option
                        key={t.id}
                        value={t.id}
                        disabled={t.id !== item.tag && pickedTagIds.includes(t.id)}
                      >
                        {t.name} ({t.data_type}, #{t.id})
                      </option>
                    ))}
                  </select>
                </>
              ) : (
                <>
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground w-9">
                    Const:
                  </span>
                  <input
                    type="number"
                    step="any"
                    className="h-7 text-xs bg-card border border-border rounded px-2 flex-1"
                    value={item.value}
                    onChange={(e) => {
                      const raw = e.target.value;
                      const n = raw === "" ? 0 : Number(raw);
                      updateRow(idx, { value: Number.isFinite(n) ? n : 0 });
                    }}
                  />
                </>
              )}

              <button
                type="button"
                onClick={() => removeRow(idx)}
                className="h-7 w-7 inline-flex items-center justify-center rounded
                           text-muted-foreground hover:bg-secondary hover:text-destructive"
                title="Remove this input"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 mt-2">
        <button
          type="button"
          onClick={addTagRow}
          disabled={items.length >= maxItems}
          className="text-[11px] px-2 py-1 rounded border border-border
                     hover:bg-secondary inline-flex items-center gap-1
                     disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <Plus className="h-3 w-3" /> Add tag
        </button>
        <button
          type="button"
          onClick={addConstRow}
          disabled={items.length >= maxItems}
          className="text-[11px] px-2 py-1 rounded border border-border
                     hover:bg-secondary inline-flex items-center gap-1
                     disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <Plus className="h-3 w-3" /> Add constant
        </button>
        <span className="text-[10px] text-muted-foreground ml-auto">
          {items.length} / max {maxItems}
          {items.length < minItems && (
            <span className="text-destructive ml-2">need at least {minItems}</span>
          )}
        </span>
      </div>

      {fieldHelp(field)}
    </div>
  );
}


export function FieldRenderer(props: FieldProps) {
  switch (props.field.type) {
    case "tag_ref":               return <TagRefField {...props} />;
    case "tag_ref_list":          return <TagRefListField {...props} />;
    case "tag_or_constant":       return <TagOrConstantField {...props} />;
    case "tag_or_constant_list":  return <TagOrConstantListField {...props} />;
    case "integer":               return <IntegerField {...props} />;
    case "number":                return <NumberField {...props} />;
    case "number_list":           return <NumberListField {...props} />;
    case "boolean":               return <BooleanField {...props} />;
    case "enum":                  return <EnumField {...props} />;
    case "mode_select":           return <ModeSelectField {...props} />;
    default:
      return (
        <div className="flex items-center gap-2 text-xs text-destructive">
          <AlertTriangle className="h-3 w-3" />
          Unsupported field type: {(props.field as any).type}
        </div>
      );
  }
}
