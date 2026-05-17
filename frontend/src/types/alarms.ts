/**
 * Phase 14.5 — alarms UI types.
 *
 * Mirrors the Pydantic models in backend/app/api/alarms.py. Keep these
 * in lockstep with the backend; if you add a severity or rule_type
 * there, add it here.
 */

export type RuleType =
  | "hi_hi"
  | "hi"
  | "lo"
  | "lo_lo"
  | "deviation"
  | "rate_of_change";

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export type EventType =
  | "activated"
  | "cleared"
  | "acked"
  | "shelved"
  | "unshelved"
  | "disabled"
  | "enabled";

export type StateValue =
  | "normal"
  | "active_unack"
  | "active_ack"
  | "inactive_unack"
  | "shelved"
  | "disabled";

// Which rule_types the evaluator currently handles. The other two
// (deviation, rate_of_change) are accepted by the API but skipped by
// the evaluator until 14.3b. The form hides them so the operator
// doesn't configure something that silently won't fire.
export const EVALUABLE_RULE_TYPES: RuleType[] = ["hi_hi", "hi", "lo", "lo_lo"];

export const RULE_TYPE_LABELS: Record<RuleType, string> = {
  hi_hi: "High-High",
  hi: "High",
  lo: "Low",
  lo_lo: "Low-Low",
  deviation: "Deviation",
  rate_of_change: "Rate of Change",
};

export const SEVERITY_LABELS: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

export const STATE_LABELS: Record<StateValue, string> = {
  normal: "Normal",
  active_unack: "Active (unacked)",
  active_ack: "Active (acked)",
  inactive_unack: "Cleared (unacked)",
  shelved: "Shelved",
  disabled: "Disabled",
};

// Sort ordering for severity, used in client-side comparisons that the
// API doesn't already pre-sort.
export const SEVERITY_RANK: Record<Severity, number> = {
  critical: 1,
  high: 2,
  medium: 3,
  low: 4,
  info: 5,
};

export interface AlarmRule {
  id: number;
  tag_id: number;
  tag_name: string | null;
  rule_type: RuleType;
  severity: Severity;
  threshold: number;
  deadband: number;
  on_delay_sec: number;
  off_delay_sec: number;
  latched: boolean;
  enabled: boolean;
  message_template: string | null;
  // Phase 14.7 — rolling window for deviation / rate_of_change.
  // Null for other rule types; null also falls back to evaluator
  // default (60s) when set on a dev/RoC rule.
  window_seconds: number | null;
  created_at: string;
  updated_at: string;
}

export interface AlarmActive {
  rule_id: number;
  tag_id: number;
  tag_name: string;
  engineering_unit: string | null;
  rule_type: RuleType;
  severity: Severity;
  threshold: number;
  state: StateValue;
  last_change_time: string;
  current_value: number | null;
  current_quality: number | null;
  last_ack_user_id: number | null;
  last_ack_time: string | null;
  // Phase 14.4 — populated only for state='shelved'; null otherwise
  shelved_until: string | null;
  shelve_user_id: number | null;
  message_template: string | null;
}

export interface ShelveRequest {
  duration_minutes: number;
  user_id: number | null;
  comment: string | null;
}

export interface AlarmEvent {
  id: number;
  rule_id: number;
  tag_id: number;
  tag_name: string | null;
  event_time: string;
  event_type: EventType;
  value: number | null;
  quality: number | null;
  user_id: number | null;
  comment: string | null;
}

export interface AlarmRuleCreate {
  tag_id: number;
  rule_type: RuleType;
  severity: Severity;
  threshold: number;
  deadband: number;
  on_delay_sec: number;
  off_delay_sec: number;
  latched: boolean;
  enabled: boolean;
  message_template: string | null;
  window_seconds: number | null;
}

export type AlarmRuleUpdate = Partial<Omit<AlarmRuleCreate, "tag_id">>;
