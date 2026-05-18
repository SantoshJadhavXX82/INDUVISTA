/**
 * Phase 14.11 - Types for the alarm rules bulk import modal.
 *
 * These mirror the ImportSummary / RowResult dataclasses in
 * backend/app/services/alarm_rule_import.py.
 */

export type ImportRowStatus = "ok" | "error" | "duplicate";

export interface ImportRowProposed {
  tag_id: number;
  tag_name: string;
  rule_type: string;
  severity: string;
  threshold: number;
  deadband: number;
  on_delay_sec: number;
  off_delay_sec: number;
  latched: boolean;
  enabled: boolean;
  window_seconds: number | null;
  message_template: string | null;
}

export interface ImportRowResult {
  row_number: number;
  tag_name: string;
  rule_type: string;
  severity: string;
  threshold: number | null;
  status: ImportRowStatus;
  errors: string[];
  warnings: string[];
  proposed: ImportRowProposed | null;
}

export interface ImportSummary {
  total_rows: number;
  ok_count: number;
  error_count: number;
  duplicate_count: number;
  warning_count: number;
  rows: ImportRowResult[];
  dry_run: boolean;
  committed: boolean;
}
