/**
 * Client-side resolver for named_set display text.
 *
 * The Live API returns the named_set_id but not the per-value display_text
 * (that would require a per-row JOIN). Instead the frontend fetches the
 * named_sets master once (with values, cached for 60s) and looks up
 * (set_id, raw_value) → display_text locally.
 *
 * Phase 8.3
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { NamedSet } from "@/types/api";

/** Lookup map: setId -> rawValue -> display info. */
export type NamedSetMap = Map<number, Map<number, { text: string; color: string | null }>>;

/** Fetch all named sets (with values) and return a fast lookup map. */
export function useNamedSetMap(): { map: NamedSetMap; loading: boolean } {
  const query = useQuery<NamedSet[]>({
    queryKey: ["named-sets", "with-values"],
    queryFn: () => api.get("/named-sets?include_values=true"),
    staleTime: 60_000,
  });

  const map = useMemo<NamedSetMap>(() => {
    const m = new Map<number, Map<number, { text: string; color: string | null }>>();
    (query.data ?? []).forEach((s) => {
      const inner = new Map<number, { text: string; color: string | null }>();
      s.values.forEach((v) => {
        inner.set(v.raw_value, { text: v.display_text, color: v.color });
      });
      m.set(s.id, inner);
    });
    return m;
  }, [query.data]);

  return { map, loading: query.isLoading };
}

/**
 * Resolve a (named_set_id, raw_value) pair to display text.
 * Returns null if either is missing or unmapped.
 */
export function resolveNamedSet(
  map: NamedSetMap,
  setId: number | null | undefined,
  rawValue: number | null | undefined,
): { text: string; color: string | null } | null {
  if (setId == null || rawValue == null) return null;
  return map.get(setId)?.get(rawValue) ?? null;
}
