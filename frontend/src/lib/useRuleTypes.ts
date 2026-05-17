/**
 * Phase 14.6b — Rule type list hook.
 *
 * Centralised React Query fetch of /api/alarms/rule-types. Same
 * caching strategy as useSeverities — the list rarely changes, so
 * 60-second staleTime keeps everything in sync without API thrash.
 *
 * Used by:
 *   - AlarmsRules (rule form rule_type dropdown)
 *   - AlarmRuleTypesAdmin (the admin page itself)
 *   - Future: rule-row badge rendering with dynamic labels
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { AlarmRuleType } from "@/types/alarmRuleTypes";

export const RULE_TYPES_QUERY_KEY = ["alarm-rule-types"] as const;

export function useRuleTypes() {
  return useQuery({
    queryKey: RULE_TYPES_QUERY_KEY,
    queryFn: () => api.get<AlarmRuleType[]>("/alarms/rule-types"),
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });
}

export function findRuleTypeByCode(
  ruleTypes: AlarmRuleType[],
  code: string,
): AlarmRuleType | undefined {
  return ruleTypes.find((rt) => rt.code === code);
}
