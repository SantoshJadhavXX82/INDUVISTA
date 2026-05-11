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
  engineering_unit: string | null;
  groups: string[];
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
  value_double: number | null;
  value_text: string | null;
  st: number | null;
  st_reason: string | null;
  time: string | null;
  age_seconds: number | null;
};

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
