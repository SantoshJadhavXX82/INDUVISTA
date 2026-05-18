/**
 * Phase 16.0b - Schema-driven calc block form.
 *
 * Single entry point: pass a block code + the current draft of
 * block_config + an onChange callback. The form looks up the schema
 * via useBlockSchemas() and renders one field per schema entry using
 * FieldRenderer from ./fields.
 *
 * The form is uncontrolled-from-the-outside-but-controlled-from-the-
 * parent: parent owns blockConfig state, form just edits it.
 *
 * Empty block_config draft is fine on first render - field components
 * handle missing keys gracefully and produce a config that the
 * backend will then validate via the block's validate_config(). The
 * UI provides hints (required markers, dtype filters, min/max) but
 * the BACKEND is the authoritative validator.
 */
import { AlertTriangle, Loader2 } from "lucide-react";

import { useBlockSchemas } from "@/lib/useBlockSchemas";
import type { BlockConfigDraft } from "@/types/calcBlockSchemas";
import { FieldRenderer } from "./fields";


interface CalcBlockFormProps {
  blockCode: string;
  blockConfig: BlockConfigDraft;
  onChange: (next: BlockConfigDraft) => void;
}


export function CalcBlockForm({
  blockCode, blockConfig, onChange,
}: CalcBlockFormProps) {
  const schemas = useBlockSchemas();

  if (schemas.isLoading) {
    return (
      <p className="text-xs text-muted-foreground flex items-center gap-2 py-4">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading block schemas…
      </p>
    );
  }

  if (schemas.isError) {
    return (
      <div className="flex items-start gap-2 text-xs text-destructive py-2">
        <AlertTriangle className="h-3 w-3 flex-shrink-0 mt-0.5" />
        <span>
          Failed to load schemas: {(schemas.error as Error)?.message}
        </span>
      </div>
    );
  }

  const schema = schemas.data?.[blockCode];

  if (!blockCode) {
    return (
      <p className="text-xs text-muted-foreground italic py-2">
        Select a block type to configure.
      </p>
    );
  }

  if (!schema) {
    return (
      <div className="flex items-start gap-2 text-xs text-amber-700 py-2">
        <AlertTriangle className="h-3 w-3 flex-shrink-0 mt-0.5" />
        <span>
          No schema available for block type <code>{blockCode}</code>. You
          can still create this calc via POST /api/calc/definitions with
          a hand-written block_config.
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {schema.description && (
        <p className="text-[11px] text-muted-foreground italic border-l-2 border-border pl-2">
          {schema.description}
        </p>
      )}
      {schema.fields.map((field) => (
        <FieldRenderer
          key={field.key}
          field={field}
          blockConfig={blockConfig}
          onChange={onChange}
        />
      ))}
    </div>
  );
}
