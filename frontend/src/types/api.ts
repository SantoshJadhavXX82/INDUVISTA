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

// Phase 12.6 — system resources (Diagnostics page)
export type CpuStats = {
  percent: number;
  count_logical: number;
  count_physical: number | null;
  load_average: number[] | null;
};

export type MemoryStats = {
  total_bytes: number;
  used_bytes: number;
  available_bytes: number;
  cached_bytes: number;
  percent: number;
};

export type DiskUsage = {
  mountpoint: string;
  device: string | null;
  fstype: string | null;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  percent: number;
};

export type GpuStats = {
  index: number;
  name: string;
  utilization_percent: number;
  memory_total_bytes: number;
  memory_used_bytes: number;
  memory_percent: number;
  temperature_c: number | null;
};

export type ProcessInfo = {
  pid: number;
  name: string;
  cpu_percent: number;
  memory_bytes: number;
  memory_percent: number;
  threads: number;
  started_at: string | null;
  is_self: boolean;
};

export type SystemStats = {
  // 'host' = pushed in by the host-agent (real Task Manager / top numbers).
  // 'container' = fallback from psutil inside the backend container.
  scope: "host" | "container";
  hostname: string | null;
  platform: string | null;          // "Windows" / "Linux" / "Darwin"
  host_agent_last_seen_sec: number | null;
  timestamp: string;
  uptime_sec: number;
  cpu: CpuStats;
  memory: MemoryStats;
  disks: DiskUsage[];
  gpus: GpuStats[];
  top_processes: ProcessInfo[];
};

export type OutOfRangeTag = {
  tag_id: number;
  tag_name: string;
  device_id: number;
  device_name: string;
  value_double: number | null;
  engineering_unit: string | null;
  min_value: number | null;
  max_value: number | null;
  violation: "LOW" | "HIGH";
  last_seen: string;
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
  /**
   * Phase 23.8 — display precision (digits after decimal point) for
   * this tag in the UI. NULL = auto (magnitude-based heuristic).
   * Storage precision is set by data_type; this is purely cosmetic.
   */
  decimal_places: number | null;
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

// Phase 12.3 — pair tag live view. One row per (pair, name), resolved
// to whichever side of the pair is currently the duty.
export type PairTagLive = {
  kind: "pair";
  pair_tag_id: number;
  tag_name: string;
  data_type: string;
  function_code: number;
  address: number;
  engineering_unit: string | null;
  // The "active" side is whoever is currently duty.
  active_device_id: number | null;
  active_device_name: string | null;
  active_tag_id: number | null;
  // Both sides of the pair, for context display.
  primary_device_id: number;
  primary_device_name: string;
  primary_device_duty_role: string;
  partner_device_id: number;
  partner_device_name: string;
  partner_device_duty_role: string;
  /** Phase 12.5 — true if either side has manual_override set. */
  pair_manual_override: boolean;
  value_double: number | null;
  value_text: string | null;
  time: string | null;
  st: number | null;
  st_reason: string | null;
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

/** Register Browser (Phase 7 — C4, extended in 10.2 for Enron) */
export type ScanRow = {
  address: number;
  hex: string;
  value: number;
  // Phase 10.2 — populated only when the backend ran an Enron read with a
  // known value width. Standard reads leave these undefined and the frontend
  // pairs consecutive rows for 32/64-bit interpretations.
  decoded_float32_abcd?: number | null;
  decoded_float32_dcba?: number | null;
  decoded_int32?: number | null;
  decoded_uint32?: number | null;
  decoded_float64_abcd?: number | null;
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

// ---------------------------------------------------------------------------
// Phase 13.1 — Trend module
// ---------------------------------------------------------------------------
// These types mirror backend/app/api/trends.py exactly. If the backend's
// Pydantic schemas change, regenerate the corresponding types here.

export type TrendTag = {
  id: number;
  name: string;
  description: string | null;
  device_id: number;
  device_name: string;
  channel_id: number;
  channel_name: string;
  protocol: string | null;
  register_block_id: number | null;
  register_block_name: string | null;
  address: number | null;
  data_type: string;
  engineering_unit: string | null;
  logging_enabled: boolean;
  min_value: number | null;
  max_value: number | null;
  current_value_double: number | null;
  current_value_text: string | null;
  current_st: number | null;
  // Derived from current_st by the backend: 'good' | 'uncertain' | 'bad'
  // (or null when there's no reading yet).
  current_quality: string | null;
  last_update_utc: string | null;
};

// Single point on the trend chart. Short field names keep payloads small
// for 1000+ point series. Optional fields populated only for the
// corresponding aggregation level — `mn`/`mx`/`g`/`b` for buckets,
// `st`/`src` for raw points.
export type TrendPoint = {
  t: string;                // ISO timestamp (UTC)
  v: number | null;         // value_double
  vt?: string | null;       // value_text (non-numeric tags)
  mn?: number | null;       // bucket min (aggregated rows only)
  mx?: number | null;       // bucket max (aggregated rows only)
  st?: number;              // ST quality (raw rows only)
  src?: string | null;      // source (raw rows only)
  g?: number | null;        // good_count in bucket
  b?: number | null;        // bad_count in bucket
};

export type TrendSeries = {
  tag_id: number;
  tag_name: string;
  engineering_unit: string | null;
  data_type: string;
  min_value: number | null;
  max_value: number | null;
  decimal_places: number | null;   // Phase 23.9 — display precision
  aggregation: "raw" | "1m" | "1h" | "1d";
  raw_count: number;        // total samples in window before downsampling
  returned_count: number;   // points in this response
  points: TrendPoint[];
};

export type TrendHistoryResponse = {
  start: string;
  end: string;
  aggregation: "raw" | "1m" | "1h" | "1d";
  series: TrendSeries[];
};

export type TagAvailability = {
  tag_id: number;
  tag_name: string;
  expected_samples: number;
  actual_samples: number;
  good_samples: number;
  uncertain_samples: number;
  bad_samples: number;
  missing_samples: number;
  availability_pct: number;
  good_availability_pct: number;
  longest_gap_sec: number | null;
  longest_gap_start: string | null;
  first_sample: string | null;
  last_sample: string | null;
  // Phase 13.11c — value statistics over good samples
  engineering_unit: string | null;
  decimal_places: number | null;   // Phase 23.9 — display precision (NULL = auto)
  mean_value: number | null;
  stddev_value: number | null;
  observed_min: number | null;
  observed_max: number | null;
};

export type TrendSummaryResponse = {
  start: string;
  end: string;
  tags: TagAvailability[];
};

// ---------------------------------------------------------------------------
// Phase 13.4 — Saved trend views
// ---------------------------------------------------------------------------

export type TrendViewConfig = {
  tag_ids: number[];
  mode: "historical" | "live";
  preset_minutes?: number | null;
  preset_label?: string | null;
  start?: string | null;
  end?: string | null;
};

export type TrendView = {
  id: number;
  name: string;
  description: string | null;
  config: TrendViewConfig;
  created_at: string;
  updated_at: string;
};

export type TrendViewCreate = {
  name: string;
  description?: string | null;
  config: TrendViewConfig;
};

// ---------------------------------------------------------------------------
// Raw historical data table (spec section 7.4)
// ---------------------------------------------------------------------------

export type RawTableRow = {
  t: string;                              // ISO timestamp (UTC)
  tag_id: number;
  tag_name: string;
  v: number | null;
  vt: string | null;
  engineering_unit: string | null;
  decimal_places: number | null;          // Phase 23.9 — display precision (NULL = auto)
  st: number | null;
  st_class: string | null;                // good / uncertain / bad
  device_name: string;
  protocol: string | null;
  channel_name: string;
  register_block_name: string | null;
  address: number | null;
  data_type: string;
  source: string | null;
};

export type RawTableResponse = {
  start: string;
  end: string;
  rows: RawTableRow[];
  returned: number;
  limit: number;
  truncated: boolean;
};


// ─── OPC UA — Phase OPC-web.3 ───────────────────────────────────────

/** A configured OPC UA endpoint that the backend subscribes to. */
export type OpcSourceResponse = {
  id: number;
  name: string;
  description: string | null;
  endpoint: string;
  security_policy:
    | "None" | "Basic128Rsa15" | "Basic256" | "Basic256Sha256"
    | "Aes128_Sha256_RsaOaep" | "Aes256_Sha256_RsaPss";
  username: string;
  // password never returned by GET — security boundary
  publishing_interval_ms: number;
  reconnect_min_sec: number;
  reconnect_max_sec: number;
  is_enabled: boolean;
  channel_id: number;
  device_id: number;
  created_at: string;
  updated_at: string;
  mapping_count: number;
  /** Most recent tag_value.time for any tag bound to this source's
   *  synthetic device. NULL = no samples ever landed. The UI derives
   *  Live / Stale / Idle from how recent this is. */
  last_sample_at: string | null;
  // Phase OPC-web.2.2 trust_server_timestamp - when true, the worker
  // uses DataValue.SourceTimestamp; when false (default) it uses
  // ingest-time UTC. Set true only for production servers with
  // verified clock sync. See migration 0055.
  trust_server_timestamp: boolean;
  // Phase OPC-web.2.3 server clock drift - worker-measured offset
  // between the OPC server's clock and the worker's clock at the
  // last subscription activation. The modal uses these to warn the
  // operator before they enable trust_server_timestamp.
  // drift_sec: positive means server is ahead of worker
  // check_at: when drift was measured; null if never
  last_server_clock_drift_sec: number | null;
  last_server_clock_check_at: string | null;
};

// Phase OPC-web.2.5 OPC diagnostics panel — shape returned by
// GET /api/diagnostics/opc-sources (OPC-native runtime state).
export type OpcSourceDiag = {
  source_id: number;
  name: string;
  endpoint: string;
  enabled: boolean;
  mapping_count: number;
  publishing_interval_ms: number | null;
  last_sample_at: string | null;
  seconds_since_last_sample: number | null;
  last_server_clock_drift_sec: number | null;
  last_server_clock_check_at: string | null;
  state: "disabled" | "live" | "idle" | "stale" | "lost";
};

export type OpcSourceCreate = {
  name: string;
  description?: string | null;
  endpoint: string;
  security_policy: OpcSourceResponse["security_policy"];
  username?: string;
  password?: string;
  publishing_interval_ms?: number;
  reconnect_min_sec?: number;
  reconnect_max_sec?: number;
  is_enabled?: boolean;
  trust_server_timestamp?: boolean;
};

export type OpcSourceUpdate = Partial<OpcSourceCreate>;

/** A NodeId → tag_id mapping on one OPC source. */
export type OpcMappingResponse = {
  id: number;
  opc_source_id: number;
  node_id: string;
  tag_id: number;
  tag_name: string;
  data_type: string;
  created_at: string;
};

export type OpcMappingCreate = {
  node_id: string;
  tag_name: string;
  tag_description?: string | null;
  data_type: string;
  engineering_unit?: string | null;
  decimal_places?: number | null;
  scale?: number;
  offset?: number;
  min_value?: number | null;
  max_value?: number | null;
};
