/**
 * Phase 14.6b — Alarm rule type types.
 *
 * Mirrors backend/app/api/alarm_rule_types.py RuleTypeResponse.
 *
 * Important: `is_evaluable` is system-managed (only migrations flip it).
 * The form should warn when the operator picks a non-evaluable type
 * since the evaluator has no logic for it and rules will never fire.
 */

export interface AlarmRuleType {
  id: number;
  code: string;
  label: string;
  description: string | null;
  rank: number;
  is_system: boolean;
  is_evaluable: boolean;
  in_use_count: number;
  created_at: string;
  updated_at: string;
}

export interface AlarmRuleTypeCreate {
  code: string;
  label: string;
  description: string | null;
  rank: number;
}

export interface AlarmRuleTypeUpdate {
  label?: string;
  description?: string | null;
  rank?: number;
}
