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
import { api } from "@/lib/api";


async function fetchBlockSchemas(): Promise<BlockSchemaMap> {
  // Use the shared api client so the bearer token is attached. A raw fetch
  // sent no Authorization header, so the RBAC middleware returned HTTP 401.
  return api.get<BlockSchemaMap>("/calc/block-schemas");
}


export function useBlockSchemas() {
  return useQuery({
    queryKey: ["calc-block-schemas"],
    queryFn: fetchBlockSchemas,
    staleTime: 24 * 60 * 60 * 1000,   // 24h: schemas only change on deploy
  });
}
