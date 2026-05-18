/**
 * Phase 15.3 - Type definitions for the calc admin UI.
 *
 * Mirrors the Pydantic response models in app/api/calc.py:
 *   - CalcDefinitionResponse (with joined stats)
 *   - BlockTypeResponse
 */

export interface CalcDefinition {
  id: number;
  tag_id: number;
  tag_name: string | null;
  block_type: string;
  block_config: Record<string, unknown>;
  enabled: boolean;
  execution_rate_ms: number;
  created_at: string;
  updated_at: string;
  // Joined stats - null when never executed
  last_executed_at: string | null;
  last_duration_ms: number | null;
  last_status: string | null;
  total_executions: number;
  total_overruns: number;
  total_errors: number;
}

export interface BlockType {
  id: number;
  code: string;
  label: string;
  category: string;
  description: string | null;
  rank: number;
  is_evaluable: boolean;
  has_registry_entry: boolean;
  created_at: string;
  updated_at: string;
}
