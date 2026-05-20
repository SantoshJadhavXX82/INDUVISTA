/**
 * Phase 17.0a - Schema-driven calc block form (with frontend overrides).
 * Phase 17.0b - Adds live preview chip after the form fields.
 *
 * Same entry contract as Phase 16.0b: pass blockCode + blockConfig +
 * onChange, the form looks up the backend schema and renders one
 * field per schema entry via FieldRenderer.
 *
 * Phase 17.0a change: applies BLOCK_SCHEMA_OVERRIDES from
 * lib/blockSchemaOverrides.ts to the fetched schema's fields BEFORE
 * rendering. This lets us override generic labels ("Left Operand"
 * -> "Minuend") and upgrade tag_ref fields to tag_or_constant per
 * block-type without changing the backend.
 *
 * Phase 17.0b change: renders <BlockPreviewChip /> at the bottom of
 * the form. The chip shows the predicted block output value in real
 * time as the user edits config (stateless blocks run in-browser via
 * blockPreview.ts; stateful blocks debounce 300ms and call
 * /api/computed-tags/preview).
 *
 * The override is pure: it returns a new array. The original schema
 * from react-query's cache is not mutated, so other consumers (if any)
 * see the unmodified backend schema.
 */
import { useMemo } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { useBlockSchemas } from "@/lib/useBlockSchemas";
import { applyBlockOverrides } from "@/lib/blockSchemaOverrides";
import type { BlockConfigDraft } from "@/types/calcBlockSchemas";
import { FieldRenderer } from "./fields";
import { BlockPreviewChip } from "./BlockPreviewChip";


interface CalcBlockFormProps {
  blockCode: string;
  blockConfig: BlockConfigDraft;
  onChange: (next: BlockConfigDraft) => void;
}


export function CalcBlockForm({
  blockCode, blockConfig, onChange,
}: CalcBlockFormProps) {
  const schemas = useBlockSchemas();

  // Pure derivation: apply frontend overrides to the backend-fetched
  // schema's fields. Recomputed only when the schema or blockCode
  // changes.
  const effectiveFields = useMemo(() => {
    const schema = schemas.data?.[blockCode];
    if (!schema) return [];
    return applyBlockOverrides(blockCode, schema.fields);
  }, [schemas.data, blockCode]);

  if (schemas.isLoading) {
    return (
      <p className="text-xs text-muted-foreground flex items-center gap-2 py-4">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading block schemas...
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
          can still create this calc via POST /api/computed-tags with a
          hand-written block_config.
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
      {effectiveFields.map((field) => (
        <FieldRenderer
          key={field.key}
          field={field}
          blockConfig={blockConfig}
          onChange={onChange}
        />
      ))}
      <BlockPreviewChip blockCode={blockCode} blockConfig={blockConfig} />
    </div>
  );
}
