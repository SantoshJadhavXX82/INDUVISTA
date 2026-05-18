/**
 * Phase 16.0b - Lightweight tags list hook for form pickers.
 *
 * Standalone implementation - doesn't assume an existing useTags hook
 * in the codebase. If you already have one with the same shape, swap
 * the imports and delete this file.
 *
 * Returns the minimal columns needed by the calc-block form pickers:
 * id, name, data_type, device_id. If the /api/tags response includes
 * more columns, they're ignored.
 */
import { useQuery } from "@tanstack/react-query";


export interface TagListItem {
  id: number;
  name: string;
  data_type: string;
  device_id: number;
}


async function fetchTags(): Promise<TagListItem[]> {
  const res = await fetch("/api/tags");
  if (!res.ok) {
    throw new Error(`Failed to fetch tags: HTTP ${res.status}`);
  }
  const data = await res.json();
  // Defensive: API may return {tags: [...]} or a bare array. Normalize.
  if (Array.isArray(data)) return data;
  if (Array.isArray((data as any).tags)) return (data as any).tags;
  if (Array.isArray((data as any).items)) return (data as any).items;
  return [];
}


export function useTagsList() {
  return useQuery({
    queryKey: ["tags-list"],
    queryFn: fetchTags,
    staleTime: 60 * 1000,   // 60s - tags don't churn fast
  });
}
