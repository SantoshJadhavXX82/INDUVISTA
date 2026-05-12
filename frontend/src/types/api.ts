/**
 * TypeScript shapes mirroring backend Pydantic response models.
 * Hand-maintained — if the backend changes a schema, update here too.
 * (Future improvement: generate from OpenAPI via openapi-typescript.)
 */

export type HealthResponse = {
  status: string;
  app_name: string;
  app_env: string;
  app_timezone: string;
  role: string;
  db_latency_ms: number;
  migration_version: string | null;
  // Phase 7 E1d — system heartbeat
  uptime_sec: number;
  started_at: string;
  cycle_count: number;
};

export type DiagnosticsSummary = {
  enabled_tag_count: number;
  enabled_device_count: number;
  overlap_count: number;
  block_fit_issue_count: number;
  stale_tag_count: number;
  workers_healthy: number;
  workers_unhealthy: number;
  buffer_backlog: number;
};

export type WorkerDeviceStatus = {
  device_id: number;
  device_name: string;
  last_cycle_at: string | null;
  last_cycle_samples_total: number | null;
  last_cycle_samples_good: number | null;
  cumulative_samples_total: number;
  cumulative_samples_good: number;
  consecutive_failures: number;
  connection_state: "connected" | "disconnected" | "reconnecting";
  updated_at: string;
  seconds_since_last_cycle: number | null;
};

export type BufferHealth = {
  backlog: number;
  oldest_sample_at: string | null;
  oldest_sample_age_seconds: number | null;
  last_replay_at: string | null;
  last_replay_count: number | null;
  updated_at: string;
  status: "healthy" | "buffering" | "stuck";
};

export type TagOverlap = {
  device_id: number;
  device_name: string;
  function_code: number;
  tag1_id: number;
  tag1_name: string;
  tag1_address: number;
  tag1_register_count: number;
  tag2_id: number;
  tag2_name: string;
  tag2_address: number;
  tag2_register_count: number;
};

export type TagBlockFitIssue = {
  tag_id: number;
  tag_name: string;
  device_id: number;
  device_name: string;
  block_id: number;
  block_name: string;
  issue: string;
};

export type StaleTag = {
  tag_id: number;
  tag_name: string;
  device_id: number;
  device_name: string;
  last_seen: string;
  age_seconds: number;
  stale_after_sec: number;
  st: number;
  st_reason: string | null;
};

export type LiveTag = {
  tag_id: number;
  tag_name: string;
  description: string | null;
  // Phase 8.1: resolved display unit (master code → override → null)
  engineering_unit: string | null;
  // Raw fields for editing — exposed so Tag Explorer can distinguish FK from override
  engineering_unit_id: number | null;
  engineering_unit_override: string | null;
  unit_label: string | null;
  unit_quantity_kind: string | null;
  groups: string[];
  // Phase 8.2 — raw group IDs for membership editing
  group_ids: number[];
  // Phase 8.3 — named set ref. The resolved display_text for the current
  // value is computed client-side from a cached named_set fetch.
  named_set_id: number | null;
  named_set_name: string | null;
  data_type: string;
  device_id: number;
  device_name: string;
  register_block_id: number | null;
  register_block_name: string | null;
  function_code: number;
  address: number;
  register_count: number;
  byte_order: string;
  scale: number;
  offset: number;
  min_value: number | null;
  max_value: number | null;
  enabled: boolean;
  // Phase 7 E1a — heartbeat metadata
  is_heartbeat: boolean;
  heartbeat_max_stale_sec: number | null;
  // Phase 8.5.1 — write opt-in flags. tag-level + parent-block-level.
  // block_writable is null for unblocked tags (no parent to gate on).
  writable: boolean;
  block_writable: boolean | null;
  value_double: number | null;
  value_text: string | null;
  st: number | null;
  st_reason: string | null;
  time: string | null;
  age_seconds: number | null;
};

// Phase 8.1 — engineering_units master
export type EngineeringUnit = {
  id: number;
  code: string;
  label: string;
  quantity_kind: string | null;
  enabled: boolean;
  is_system: boolean;
  description: string | null;
  created_at: string;
  updated_at: string;
  in_use_count: number;
};

export type EngineeringUnitCreate = {
  code: string;
  label: string;
  quantity_kind?: string | null;
  enabled?: boolean;
  description?: string | null;
};

export type EngineeringUnitUpdate = Partial<EngineeringUnitCreate>;

// Phase 8.2 — groups master
export type GroupType = "AREA" | "EQUIPMENT" | "UNIT" | "PACKAGE" | "REPORT" | "CUSTOM";

export type Group = {
  id: number;
  name: string;
  description: string | null;
  group_type: GroupType;
  parent_group_id: number | null;
  parent_group_name: string | null;
  display_order: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  in_use_count: number;
};

export type GroupCreate = {
  name: string;
  description?: string | null;
  group_type?: GroupType;
  parent_group_id?: number | null;
  display_order?: number;
  enabled?: boolean;
};

export type GroupUpdate = Partial<GroupCreate>;

// Phase 8.3 — named sets master (value→label translation library)
export type NamedSetValue = {
  id: number;
  raw_value: number;
  display_text: string;
  display_order: number;
  color: string | null;
};

export type NamedSet = {
  id: number;
  name: string;
  description: string | null;
  is_system: boolean;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  value_count: number;
  in_use_count: number;
  values: NamedSetValue[];
};

export type NamedSetCreate = {
  name: string;
  description?: string | null;
  enabled?: boolean;
};

export type NamedSetUpdate = Partial<NamedSetCreate>;

export type SparklinePoint = {
  time: string;
  value: number;
};

export type TagSparkline = {
  tag_id: number;
  points: SparklinePoint[];
};

export type BulkResult = {
  row: number;
  tag_id?: number;
  block_id?: number;
  name?: string;
  error?: string;
};

/** Frame Inspector (Phase 7 Batch 2 — B1). */
export type Frame = {
  seq: number;
  timestamp: string;
  direction: "tx" | "rx";
  function_code: number;
  address: number;
  register_count: number;
  unit_id: number;
  block_name: string;
  transaction_id: number;
  hex_bytes: string;
  byte_count: number;
  latency_ms: number | null;
  error: string | null;
  summary: string | null;
};

export type FramesResponse = {
  device_id: number;
  capture_enabled: boolean;
  frames: Frame[];
};

/** Register Browser (Phase 7 — C4) */
export type ScanRow = {
  address: number;
  hex: string;
  value: number;
};

export type ScanRangeResponse = {
  device_id: number;
  function_code: number;
  start_address: number;
  end_address: number;
  elapsed_ms: number;
  chunks: number;
  rows: ScanRow[];
};
