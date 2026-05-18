/**
 * Phase 15.3 - React Query hooks for the calc admin page.
 *
 * Two reads:
 *   - GET /api/calc/definitions   (every 5s; stats change as evaluator ticks)
 *   - GET /api/calc/block-types   (cached; only changes on migrations)
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { CalcDefinition, BlockType } from "@/types/calcDefinitions";


export const CALC_DEFINITIONS_QUERY_KEY = ["calc-definitions"] as const;
export const BLOCK_TYPES_QUERY_KEY = ["calc-block-types"] as const;


export function useCalcDefinitions() {
  return useQuery({
    queryKey: CALC_DEFINITIONS_QUERY_KEY,
    queryFn: () => api.get<CalcDefinition[]>("/calc/definitions"),
    refetchInterval: 5000,
  });
}


export function useBlockTypes() {
  return useQuery({
    queryKey: BLOCK_TYPES_QUERY_KEY,
    queryFn: () => api.get<BlockType[]>("/calc/block-types"),
    staleTime: 60_000,
  });
}
