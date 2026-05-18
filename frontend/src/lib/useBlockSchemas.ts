/**
 * Phase 16.0b - Block schemas fetch hook.
 *
 * Fetches the entire block schema map from the backend once and caches
 * it. Schemas only change when the backend deploys, so the cache time
 * is long. Components that need a single block's schema just read from
 * the map by code.
 */
import { useQuery } from "@tanstack/react-query";
import type { BlockSchemaMap } from "@/types/calcBlockSchemas";


async function fetchBlockSchemas(): Promise<BlockSchemaMap> {
  const res = await fetch("/api/calc/block-schemas");
  if (!res.ok) {
    throw new Error(`Failed to fetch block schemas: HTTP ${res.status}`);
  }
  return res.json();
}


export function useBlockSchemas() {
  return useQuery({
    queryKey: ["calc-block-schemas"],
    queryFn: fetchBlockSchemas,
    staleTime: 24 * 60 * 60 * 1000,   // 24h: schemas only change on deploy
  });
}
