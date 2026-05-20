/**
 * Phase 17.0b - React Query hooks for Computed Tags + Computed Devices.
 *
 * Function names are kept stable for back-compat. The hook
 * `useCalcDefinitions` hits /api/computed-tags. Phase 17.0b adds
 * `useExternalOutputCandidates` which returns tags eligible to be a
 * computed tag's external output target (filtered to non-computed-device
 * tags that aren't already another calc's output).
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  CalcDefinition,
  ComputedDevice,
  BlockType,
  OutputTagOption,
} from "@/types/calcDefinitions";


export const CALC_DEFINITIONS_QUERY_KEY = ["computed-tags"] as const;
export const COMPUTED_DEVICES_QUERY_KEY = ["computed-devices"] as const;
export const BLOCK_TYPES_QUERY_KEY = ["calc-block-types"] as const;
export const TAGS_ALL_QUERY_KEY = ["tags", "all"] as const;
export const DEVICES_ALL_QUERY_KEY = ["devices", "all"] as const;


export function useCalcDefinitions() {
  return useQuery({
    queryKey: CALC_DEFINITIONS_QUERY_KEY,
    queryFn: () => api.get<CalcDefinition[]>("/computed-tags"),
    refetchInterval: 5000,
  });
}


export function useComputedDevices() {
  return useQuery({
    queryKey: COMPUTED_DEVICES_QUERY_KEY,
    queryFn: () => api.get<ComputedDevice[]>("/computed-devices"),
    refetchInterval: 10_000,
  });
}


export function useBlockTypes() {
  return useQuery({
    queryKey: BLOCK_TYPES_QUERY_KEY,
    queryFn: () => api.get<BlockType[]>("/calc/block-types"),
    staleTime: 60_000,
  });
}


/**
 * Phase 17.0b - tags eligible to be a computed tag's external output target.
 *
 * Returns tags filtered to:
 *   - NOT on a computed device (no chaining computed→computed in v1)
 *   - NOT already used as another computed tag's output
 *
 * `excludeCalcId` lets the edit modal keep the calc's current external
 * target in the list (without it, picking the same target twice would
 * be filtered out and look broken).
 */
export function useExternalOutputCandidates(excludeCalcId?: number) {
  const tags = useQuery({
    queryKey: TAGS_ALL_QUERY_KEY,
    queryFn: () => api.get<Array<{
      id: number;
      name: string;
      data_type: string;
      device_id: number;
    }>>("/tags"),
    staleTime: 30_000,
  });

  const devices = useQuery({
    queryKey: DEVICES_ALL_QUERY_KEY,
    queryFn: () => api.get<Array<{
      id: number;
      name: string;
      protocol: string;
    }>>("/devices"),
    staleTime: 30_000,
  });

  const calcs = useCalcDefinitions();

  return useMemo(() => {
    const isLoading = tags.isLoading || devices.isLoading || calcs.isLoading;
    if (!tags.data || !devices.data || !calcs.data) {
      return { data: undefined as OutputTagOption[] | undefined, isLoading };
    }

    const deviceMap = new Map(devices.data.map((d) => [d.id, d]));
    const takenIds = new Set<number>(
      calcs.data
        .filter((c) => c.output_tag_id != null && c.id !== excludeCalcId)
        .map((c) => c.output_tag_id as number),
    );

    const filtered: OutputTagOption[] = [];
    for (const t of tags.data) {
      const dev = deviceMap.get(t.device_id);
      if (!dev || dev.protocol === "computed") continue;
      if (takenIds.has(t.id)) continue;
      filtered.push({
        id: t.id,
        name: t.name,
        device_id: t.device_id,
        device_name: dev.name,
        data_type: t.data_type,
      });
    }

    filtered.sort((a, b) => {
      const c = a.device_name.localeCompare(b.device_name);
      return c !== 0 ? c : a.name.localeCompare(b.name);
    });

    return { data: filtered, isLoading: false };
  }, [tags.data, devices.data, calcs.data, excludeCalcId, tags.isLoading, devices.isLoading, calcs.isLoading]);
}
