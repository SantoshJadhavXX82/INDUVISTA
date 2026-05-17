/**
 * Phase 14.6 — Severity list hook.
 *
 * Centralised React Query fetch of /api/alarms/severities. The master
 * list changes rarely (only when an operator visits the admin page),
 * so a 60-second staleTime keeps every component using this hook in
 * sync without thrashing the API.
 *
 * Used by:
 *   - AlarmsRules (rule form severity dropdown)
 *   - AlarmSeveritiesAdmin (the admin page itself)
 *   - Future: SeverityBadge for dynamic colours in alarm displays
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { AlarmSeverity } from "@/types/alarmSeverities";

export const SEVERITIES_QUERY_KEY = ["alarm-severities"] as const;

export function useSeverities() {
  return useQuery({
    queryKey: SEVERITIES_QUERY_KEY,
    queryFn: () => api.get<AlarmSeverity[]>("/alarms/severities"),
    staleTime: 60_000,
    // Refetch on focus so adding a severity in another tab propagates
    refetchOnWindowFocus: true,
  });
}

/**
 * Look up a severity row by code. Returns undefined if not found.
 * Useful for resolving labels/colors when rendering rule rows.
 */
export function findSeverityByCode(
  severities: AlarmSeverity[],
  code: string,
): AlarmSeverity | undefined {
  return severities.find((s) => s.code === code);
}

/**
 * Returns the rank for sorting, or 999 for unknown codes (puts them
 * at the bottom of severity-sorted lists).
 */
export function severityRank(
  severities: AlarmSeverity[],
  code: string,
): number {
  return findSeverityByCode(severities, code)?.rank ?? 999;
}
