/**
 * Phase 14.5 — Alarm event history.
 *
 * Paginated read-only view of /api/alarms/history. Cursor pagination:
 * each "next page" fetch passes the previous batch's oldest event_time
 * as the `end` parameter, walking backwards through time. No client
 * state for total page count — events are append-only and unbounded,
 * so "page numbers" would be meaningless.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Filter, RefreshCw, AlertTriangle, ChevronRight, ChevronsRight,
} from "lucide-react";

import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { useTimeFormat } from "@/lib/timeFormat";

import type { AlarmEvent, EventType } from "@/types/alarms";

const PAGE_SIZE = 100;
const ALL_EVENT_TYPES: EventType[] = [
  "activated", "cleared",
  "acked", "shelved", "unshelved",
  "disabled", "enabled",
];

export default function AlarmsHistory() {
  const { formatDateTime } = useTimeFormat();

  // Filters
  const [eventTypeFilter, setEventTypeFilter] = useState<EventType | "all">("all");
  const [tagIdFilter, setTagIdFilter] = useState<string>("");
  // cursor: undefined for first page; ISO string for subsequent pages
  const [endCursor, setEndCursor] = useState<string | undefined>(undefined);
  // history of cursors so we can walk back
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([undefined]);

  const query = useQuery({
    queryKey: ["alarms-history", eventTypeFilter, tagIdFilter, endCursor],
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
      if (eventTypeFilter !== "all") params.set("event_type", eventTypeFilter);
      if (tagIdFilter && /^\d+$/.test(tagIdFilter)) {
        params.set("tag_id", tagIdFilter);
      }
      if (endCursor) params.set("end", endCursor);
      return api.get<AlarmEvent[]>(`/alarms/history?${params}`);
    },
    staleTime: 5_000,
  });

  const rows = query.data ?? [];
  const hasMore = rows.length === PAGE_SIZE;

  const goNext = () => {
    if (!hasMore) return;
    const oldestTime = rows[rows.length - 1].event_time;
    setCursorStack((s) => [...s, oldestTime]);
    setEndCursor(oldestTime);
  };
  const goBack = () => {
    if (cursorStack.length <= 1) return;
    const next = [...cursorStack];
    next.pop();
    setCursorStack(next);
    setEndCursor(next[next.length - 1]);
  };
  const resetFilters = () => {
    setEventTypeFilter("all");
    setTagIdFilter("");
    setEndCursor(undefined);
    setCursorStack([undefined]);
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center justify-between gap-3 flex-wrap">
          <span className="flex items-center gap-2">
            <Filter className="h-3.5 w-3.5" />
            Event history
            {query.isFetching && (
              <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />
            )}
          </span>
          <div className="flex items-center gap-2 flex-wrap">
            <label className="text-[10px] text-muted-foreground flex items-center gap-1">
              Type
              <select
                value={eventTypeFilter}
                onChange={(e) => {
                  setEventTypeFilter(e.target.value as EventType | "all");
                  setEndCursor(undefined);
                  setCursorStack([undefined]);
                }}
                className="h-7 text-xs bg-card border border-border rounded px-1.5"
              >
                <option value="all">All</option>
                {ALL_EVENT_TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </label>
            <label className="text-[10px] text-muted-foreground flex items-center gap-1">
              Tag id
              <Input
                value={tagIdFilter}
                onChange={(e) => {
                  setTagIdFilter(e.target.value);
                  setEndCursor(undefined);
                  setCursorStack([undefined]);
                }}
                placeholder="any"
                className="h-7 w-20 text-xs"
                inputMode="numeric"
              />
            </label>
            <Button variant="outline" size="sm" className="h-7 text-xs"
                    onClick={resetFilters}>
              Reset
            </Button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {query.isLoading && (
          <p className="text-xs text-muted-foreground py-6 px-3">Loading events…</p>
        )}
        {query.isError && (
          <div className="flex items-start gap-2 text-xs text-destructive py-3 px-3">
            <AlertTriangle className="h-4 w-4 flex-shrink-0" />
            <span>Failed to load: {(query.error as Error)?.message}</span>
          </div>
        )}
        {query.data && rows.length === 0 && (
          <p className="text-xs text-muted-foreground py-6 px-3">
            No events match the current filters.
          </p>
        )}
        {rows.length > 0 && (
          <>
            <div className="overflow-x-auto border-t border-border">
              <table className="w-full text-xs">
                <thead className="bg-secondary/40 text-[10px] uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium whitespace-nowrap">Time</th>
                    <th className="text-left px-3 py-2 font-medium">Tag</th>
                    <th className="text-left px-3 py-2 font-medium">Event</th>
                    <th className="text-right px-3 py-2 font-medium">Value</th>
                    <th className="text-right px-3 py-2 font-medium">Quality</th>
                    <th className="text-left px-3 py-2 font-medium">Comment</th>
                    <th className="text-right px-3 py-2 font-medium">Rule</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((e) => (
                    <tr key={`${e.id}-${e.event_time}`}
                        className="border-t border-border hover:bg-secondary/30">
                      <td className="px-3 py-1.5 tabular-nums whitespace-nowrap"
                          title={`UTC ${e.event_time}`}>
                        {formatDateTime(e.event_time)}
                      </td>
                      <td className="px-3 py-1.5">{e.tag_name ?? `#${e.tag_id}`}</td>
                      <td className="px-3 py-1.5">
                        <EventTypeBadge type={e.event_type} />
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {e.value != null ? formatNumber(e.value) : "—"}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">
                        {e.quality ?? "—"}
                      </td>
                      <td className="px-3 py-1.5 text-muted-foreground">
                        {e.comment ?? ""}
                      </td>
                      <td className="px-3 py-1.5 text-right text-muted-foreground tabular-nums">
                        #{e.rule_id}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* Pagination */}
            <div className="flex items-center justify-between text-xs px-3 py-2 border-t border-border">
              <span className="text-muted-foreground">
                Showing {rows.length} events
                {hasMore && " (more available)"}
              </span>
              <div className="flex items-center gap-1">
                <Button variant="outline" size="sm" className="h-7 text-xs"
                        disabled={cursorStack.length <= 1}
                        onClick={goBack}>
                  ‹ Newer
                </Button>
                <Button variant="outline" size="sm" className="h-7 text-xs gap-1"
                        disabled={!hasMore}
                        onClick={goNext}>
                  Older
                  <ChevronRight className="h-3 w-3" />
                </Button>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function EventTypeBadge({ type }: { type: EventType }) {
  return (
    <Badge variant="outline" className={`text-[10px] ${eventTypeClass(type)}`}>
      {type}
    </Badge>
  );
}

function eventTypeClass(type: EventType): string {
  switch (type) {
    case "activated":
      return "bg-red-50 text-red-800 border-red-300 font-semibold";
    case "cleared":
      return "bg-emerald-50 text-emerald-800 border-emerald-300";
    case "acked":
      return "bg-blue-50 text-blue-800 border-blue-300";
    case "shelved":
      return "bg-indigo-50 text-indigo-800 border-indigo-300";
    case "unshelved":
      return "bg-cyan-50 text-cyan-800 border-cyan-300";
    case "disabled":
      return "bg-slate-50 text-slate-700 border-slate-300";
    case "enabled":
      return "bg-emerald-50 text-emerald-800 border-emerald-300";
  }
}

function formatNumber(v: number): string {
  const abs = Math.abs(v);
  let s: string;
  if (abs >= 1000)    s = v.toFixed(1);
  else if (abs >= 1)  s = v.toFixed(3);
  else                s = v.toFixed(4);
  return s.replace(/\.?0+$/, "");
}
