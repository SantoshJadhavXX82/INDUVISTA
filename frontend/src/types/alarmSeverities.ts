/**
 * Phase 14.6 — Alarm severity types.
 *
 * Mirrors backend/app/api/alarm_severities.py SeverityResponse.
 */

export interface AlarmSeverity {
  id: number;
  code: string;
  label: string;
  color_hex: string;
  rank: number;
  is_system: boolean;
  in_use_count: number;
  created_at: string;
  updated_at: string;
}

export interface AlarmSeverityCreate {
  code: string;
  label: string;
  color_hex: string;
  rank: number;
}

export interface AlarmSeverityUpdate {
  label?: string;
  color_hex?: string;
  rank?: number;
}
