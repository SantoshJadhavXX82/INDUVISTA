/**
 * Phase 17.0b - Types for the Computed Tags UI.
 *
 * The `CalcDefinition` type name is kept for back-compat with anything
 * else that imports it, but its shape now matches the new
 * /api/computed-tags response (composite tag + computed_tags row).
 *
 * Phase 17.0b adds output target fields - computed tags can write
 * to either their own internal anchor (default) or to an external
 * tag elsewhere in the system.
 */

export interface CalcDefinition {
  id: number;                              // = tags.id = computed_tags.id

  // Tag-level fields (joined from `tags` table)
  device_id: number;
  device_name: string;
  name: string;
  data_type: string;
  description: string | null;
  engineering_unit: string | null;
  engineering_unit_id: number | null;
  named_set_id: number | null;
  min_value: number | null;
  max_value: number | null;

  // Computed_tags-level fields
  block_type: string;
  block_config: Record<string, unknown>;
  execution_rate_ms: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;

  // Execution stats (null when never executed)
  last_executed_at: string | null;
  last_duration_ms: number | null;
  last_status: string | null;
  last_error_message: string | null;

  // Phase 17.0b: external output target
  output_tag_id: number | null;
  output_tag_name: string | null;
  output_device_id: number | null;
  output_device_name: string | null;
}


export interface ComputedDevice {
  id: number;
  channel_id: number;
  channel_name: string;
  name: string;
  description: string | null;
  protocol: string;                        // always 'computed'
  enabled: boolean;
  scan_interval_ms: number;
  computed_tag_count: number;
  created_at: string;
  updated_at: string;
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


/** Minimal channel info needed by the create-device modal. */
export interface ChannelOption {
  id: number;
  name: string;
  enabled: boolean;
}


/**
 * Phase 17.0b - a tag eligible to be a computed tag's external output target.
 * The picker filters out tags on computed devices (no chaining) and tags
 * already used as another calc's output.
 */
export interface OutputTagOption {
  id: number;
  name: string;
  device_id: number;
  device_name: string;
  data_type: string;
}
