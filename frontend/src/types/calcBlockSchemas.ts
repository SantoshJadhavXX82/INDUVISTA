/**
 * Phase 16.0b - Types for self-describing calc block schemas.
 *
 * Mirrors backend/app/workers/calc_blocks/calc_block_schemas.py.
 *
 * mode_select fields (added for ADD/MUL N-ary support) declare 2+
 * modes; each mode has its own sub-fields. The form renderer shows
 * a radio for mode selection plus the active mode's sub-fields.
 * The mode_select field's own `key` is a virtual form-state key; it
 * is NOT stored in block_config. Sub-fields' keys are what gets
 * saved.
 */

export type FieldType =
  | "tag_ref"
  | "tag_ref_list"
  | "tag_or_constant"
  | "tag_or_constant_list"
  | "integer"
  | "number"
  | "number_list"
  | "boolean"
  | "enum"
  | "mode_select";

export interface TagFilter {
  data_type?: string[];   // restrict picker to these dtypes
}

export interface EnumOption {
  value: string | number | boolean;
  label: string;
}

export interface ModeOption {
  value: string;          // identifier ("binary", "n_ary", etc.)
  label: string;          // display label for the radio
  fields: FieldDef[];     // sub-fields rendered when this mode is active
}

export interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  required?: boolean;
  default?: unknown;
  help?: string;
  // type-specific extras
  min?: number;
  max?: number;
  minItems?: number;
  maxItems?: number;
  options?: EnumOption[] | ModeOption[];   // EnumOption for enum, ModeOption for mode_select
  filter?: TagFilter;
}

export interface BlockSchema {
  description?: string;
  fields: FieldDef[];
}

export type BlockSchemaMap = Record<string, BlockSchema>;

/**
 * Untyped block_config draft as the form edits it. Each field knows
 * how to read/write its slice.
 */
export type BlockConfigDraft = Record<string, unknown>;
