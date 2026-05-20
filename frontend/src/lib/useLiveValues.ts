/**
 * Phase 17.0c — Live tag values for the calc-block preview.
 *
 * Fetches /api/live once and returns a Map<tag_id, {value, quality}>.
 * Used by BlockPreviewChip so the inline preview shows what the block
 * would compute right now with real upstream values, instead of the
 * legacy "every tag defaults to value=1" placeholder.
 *
 * Mirrors the useTagsList pattern in the same folder. Refresh cadence
 * is intentionally slow (5s) because the preview is a what-if tool,
 * not a live dashboard; if the user wants to test specific scenarios
 * they use the "Sample inputs" override grid.
 */
import { useQuery } from "@tanstack/react-query";


export interface LiveTagValue {
  tag_id: number;
  value: number | null;
  quality: number;          // 0..255 OPC-style byte (the API field is 'st')
}


async function fetchLiveValues(): Promise<LiveTagValue[]> {
  const res = await fetch("/api/live");
  if (!res.ok) {
    throw new Error(`Failed to fetch live values: HTTP ${res.status}`);
  }
  const data = await res.json();
  // Defensive: API may return {tags: [...]} or a bare array.
  const rows: any[] = Array.isArray(data)
    ? data
    : Array.isArray(data?.tags) ? data.tags
    : Array.isArray(data?.items) ? data.items
    : [];
  // /api/live row shape (verified against backend):
  //   { tag_id, tag_name, value_double, value_text, st, st_reason,
  //     time, age_seconds, ... }
  // Bool tags carry 0/1 in value_double too. value_text is only
  // populated for string-typed tags, which we treat as null here.
  return rows.map((r) => {
    let v: number | null = null;
    if (r.value_double !== undefined && r.value_double !== null) {
      v = Number(r.value_double);
      if (!Number.isFinite(v)) v = null;
    } else if (r.value !== undefined && r.value !== null) {
      // Fallback for older response shapes
      const n = Number(r.value);
      if (Number.isFinite(n)) v = n;
    }
    return {
      tag_id: Number(r.tag_id ?? r.id),
      value: v,
      quality: Number(r.st ?? r.quality ?? 0),
    };
  });
}


/** Hook returning the live values for all tags as a Map keyed by tag id. */
export function useLiveValues() {
  const q = useQuery({
    queryKey: ["live-values"],
    queryFn: fetchLiveValues,
    staleTime: 5 * 1000,           // 5s
    refetchInterval: 5 * 1000,     // periodic refresh while open
  });
  const map = new Map<number, LiveTagValue>();
  (q.data ?? []).forEach((r) => map.set(r.tag_id, r));
  return { ...q, map };
}
