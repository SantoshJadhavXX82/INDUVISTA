/**
 * Phase 13.9 - Raw Historical Data Table (spec section 7.4).
 *
 * A sortable, paginated table that sits below the chart and shows the
 * raw underlying rows from tag_values - the actual recorded values, with
 * full per-row metadata. Always queries raw rows regardless of the chart's
 * aggregation choice, since the operator inspecting this table wants the
 * real records.
 *
 * Columns (spec 7.4):
 *   timestamp local, timestamp UTC, tag, value, EU, ST integer,
 *   quality class, device, protocol, channel, block, address.
 *   Insert time (13th column) deferred until migration 0028 lands.
 *
 * Collapsed by default to keep the chart the primary focus. Operator
 * expands when they want to inspect.
 *
 * Phase 13 closeout — focus filter:
 *   The σ-popover ("View in raw data table" button) lifts a focus tagId
 *   into Trend.tsx and passes it back as the `focusTagId` prop. When set,
 *   the table auto-opens, filters its visible rows to that tag, snaps to
 *   page 0, and shows a dismissible badge. Clearing the badge restores
 *   the unfiltered view via the onClearFocus callback.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown, ChevronRight, ChevronUp,
  Download, AlertTriangle, RefreshCw, Table as TableIcon,
  Filter, X,
} from "lucide-react";
import {
  Card, CardContent, CardHeader, CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useTimeFormat } from "@/lib/timeFormat";
import type { RawTableResponse, RawTableRow } from "@/types/api";

type Props = {
  selectedIds: number[];
  start: string | null;        // ISO; null hides the panel
  end: string | null;
  /** When non-null, the table auto-opens (if collapsed) and shows only
      rows for this tag. Set by Trend.tsx in response to the σ-popover's
      "View in raw data table" button. */
  focusTagId?: number | null;
  /** Called when the operator dismisses the focus filter badge. */
  onClearFocus?: () => void;
};

type SortKey =
  | "t" | "tag_name" | "v" | "engineering_unit" | "st" | "st_class"
  | "device_name" | "protocol" | "channel_name"
  | "register_block_name" | "address";

const PAGE_SIZE = 100;
const ROW_LIMIT_OPTIONS = [500, 1000, 2500, 5000, 10000];

export default function RawDataTable({
  selectedIds, start, end, focusTagId, onClearFocus,
}: Props) {
  const { formatDateTime } = useTimeFormat();

  // Collapsed by default - the table is for inspection, not the primary view.
  const [open, setOpen]   = useState(false);
  const [limit, setLimit] = useState(1000);
  const [page, setPage]   = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>("t");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  // Phase 13 closeout — react to external focus requests from the
  // σ-popover. Auto-open and snap back to page 0 so the operator sees
  // the filtered top row immediately.
  useEffect(() => {
    if (focusTagId != null) {
      setOpen(true);
      setPage(0);
    }
  }, [focusTagId]);

  // Only fetch when expanded - keeps the table from hammering the API
  // when the operator never opens it.
  const canQuery = open && selectedIds.length > 0 && !!start && !!end;
  const query = useQuery({
    queryKey: ["raw-table", selectedIds, start, end, limit],
    queryFn: () => {
      const params = new URLSearchParams({
        tag_ids: selectedIds.join(","),
        start: start!, end: end!,
        limit: String(limit),
        order: "desc",
      });
      return api.get<RawTableResponse>(`/trends/raw_table?${params}`);
    },
    enabled: canQuery,
    staleTime: 10_000,
  });

  // Client-side sort on the fetched rows. Backend already returns time-desc;
  // re-sort here when operator picks a different column.
  const sortedRows = useMemo(() => {
    if (!query.data) return [];
    const rows = [...query.data.rows];
    rows.sort((a, b) => cmp(a, b, sortKey, sortDir));
    return rows;
  }, [query.data, sortKey, sortDir]);

  // Apply the focus filter on top of sorting. Pagination and CSV export
  // both operate on this filtered view so the operator never sees rows
  // for other tags while the badge is visible.
  const displayedRows = useMemo(() => {
    if (focusTagId == null) return sortedRows;
    return sortedRows.filter((r) => r.tag_id === focusTagId);
  }, [sortedRows, focusTagId]);

  // Look up the focused tag's name from whichever row carries it (rows
  // include tag_name denormalised). Falls back to the numeric id if no
  // rows exist for that tag in the current window.
  const focusTagName = useMemo(() => {
    if (focusTagId == null) return null;
    const r = sortedRows.find((row) => row.tag_id === focusTagId);
    return r?.tag_name ?? `Tag #${focusTagId}`;
  }, [sortedRows, focusTagId]);

  const totalPages = Math.max(1, Math.ceil(displayedRows.length / PAGE_SIZE));
  const pageRows = displayedRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  // If the page index falls off after a refetch or filter change, snap back.
  if (page >= totalPages && page > 0) setPage(0);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "t" ? "desc" : "asc");
    }
    setPage(0);
  };

  const handleDownloadCsv = () => {
    if (displayedRows.length === 0) return;
    const csv = toCsv(displayedRows, formatDateTime);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const focusSuffix = focusTagId != null && focusTagName
      ? `-${focusTagName.replace(/[^a-z0-9_-]/gi, "_")}`
      : "";
    a.download = `induvista-raw${focusSuffix}-${(start ?? "").slice(0, 19)}_to_${(end ?? "").slice(0, 19)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-1.5 -mx-1 px-1 rounded hover:bg-secondary/40"
          >
            {open
              ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
              : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <TableIcon className="h-3.5 w-3.5" />
              Raw historical data
              {open && query.data && (
                <span className="text-[10px] text-muted-foreground font-normal">
                  {focusTagId != null
                    ? `${displayedRows.length.toLocaleString()} of ${query.data.returned.toLocaleString()} row${query.data.returned === 1 ? "" : "s"}`
                    : `${query.data.returned.toLocaleString()} row${query.data.returned === 1 ? "" : "s"}`}
                  {query.data.truncated && " (truncated)"}
                </span>
              )}
            </CardTitle>
          </button>

          {open && (
            <div className="flex items-center gap-2">
              <label className="text-[10px] text-muted-foreground flex items-center gap-1">
                Limit
                <select
                  value={limit}
                  onChange={(e) => { setLimit(parseInt(e.target.value)); setPage(0); }}
                  className="h-7 text-xs bg-card border border-border rounded px-1.5"
                >
                  {ROW_LIMIT_OPTIONS.map((n) => (
                    <option key={n} value={n}>{n.toLocaleString()}</option>
                  ))}
                </select>
              </label>
              <Button
                variant="outline" size="sm" className="h-7 text-xs gap-1"
                onClick={handleDownloadCsv}
                disabled={displayedRows.length === 0}
                title={focusTagId != null
                  ? `Download visible rows (filtered to ${focusTagName}) as CSV`
                  : "Download visible rows as CSV"}
              >
                <Download className="h-3 w-3" />
                CSV
              </Button>
              {query.isFetching && (
                <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />
              )}
            </div>
          )}
        </div>
      </CardHeader>

      {open && (
        <CardContent>
          {/* Phase 13 closeout — dismissible focus filter badge. Sits
              above any other status strips so the operator immediately
              understands the table is in a filtered state. */}
          {focusTagId != null && (
            <div
              className="text-[11px] text-indigo-800 bg-indigo-50 border border-indigo-200 rounded px-3 py-1.5 mb-2 flex items-center gap-2"
              data-raw-table-focus-badge={focusTagId}
            >
              <Filter className="h-3.5 w-3.5 flex-shrink-0 text-indigo-600" />
              <span className="flex-1">
                Filtered to <strong className="font-semibold">{focusTagName}</strong>
                <span className="text-indigo-600 ml-2">·</span>
                <span className="ml-2 text-indigo-600">
                  {displayedRows.length.toLocaleString()} row{displayedRows.length === 1 ? "" : "s"}
                </span>
              </span>
              {onClearFocus && (
                <button
                  type="button"
                  onClick={onClearFocus}
                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-indigo-700 hover:bg-indigo-100"
                  aria-label="Clear focus filter"
                >
                  <X className="h-3 w-3" />
                  Clear
                </button>
              )}
            </div>
          )}

          {selectedIds.length === 0 && (
            <p className="text-xs text-muted-foreground py-2">
              Select tags above to see raw rows.
            </p>
          )}

          {query.isLoading && (
            <p className="text-xs text-muted-foreground py-2">Loading rows…</p>
          )}

          {query.isError && (
            <div className="flex items-start gap-2 text-xs text-destructive py-2">
              <AlertTriangle className="h-4 w-4 flex-shrink-0" />
              <span>Failed to load: {(query.error as Error)?.message}</span>
            </div>
          )}

          {query.data && query.data.truncated && (
            <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-1.5 mb-2 flex items-center gap-2">
              <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
              Showing the {query.data.limit.toLocaleString()} most-recent rows
              in this window. Narrow the time range or raise the row limit
              to see more.
            </div>
          )}

          {query.data && displayedRows.length > 0 && (
            <>
              <div className="overflow-x-auto border border-border rounded">
                <table className="w-full text-xs">
                  <thead className="bg-secondary/40 text-[10px] uppercase tracking-wider text-muted-foreground">
                    <tr>
                      <SortableTh label="Timestamp"   sortKey="t"                    cur={sortKey} dir={sortDir} onClick={toggleSort} />
                      <th className="text-left px-2 py-1.5 font-medium whitespace-nowrap">UTC</th>
                      <SortableTh label="Tag"         sortKey="tag_name"             cur={sortKey} dir={sortDir} onClick={toggleSort} />
                      <SortableTh label="Value"       sortKey="v"                    cur={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                      <SortableTh label="EU"          sortKey="engineering_unit"     cur={sortKey} dir={sortDir} onClick={toggleSort} />
                      <SortableTh label="ST"          sortKey="st"                   cur={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                      <SortableTh label="Quality"     sortKey="st_class"             cur={sortKey} dir={sortDir} onClick={toggleSort} />
                      <SortableTh label="Device"      sortKey="device_name"          cur={sortKey} dir={sortDir} onClick={toggleSort} />
                      <SortableTh label="Protocol"    sortKey="protocol"             cur={sortKey} dir={sortDir} onClick={toggleSort} />
                      <SortableTh label="Channel"     sortKey="channel_name"         cur={sortKey} dir={sortDir} onClick={toggleSort} />
                      <SortableTh label="Block"       sortKey="register_block_name"  cur={sortKey} dir={sortDir} onClick={toggleSort} />
                      <SortableTh label="Addr"        sortKey="address"              cur={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((r, idx) => (
                      <tr
                        key={`${r.tag_id}-${r.t}-${idx}`}
                        className="border-t border-border hover:bg-secondary/30"
                      >
                        <td className="px-2 py-1 tabular-nums whitespace-nowrap">{formatDateTime(r.t)}</td>
                        <td className="px-2 py-1 tabular-nums whitespace-nowrap text-muted-foreground">{r.t.slice(0, 19)}Z</td>
                        <td className="px-2 py-1 whitespace-nowrap">{r.tag_name}</td>
                        <td className="px-2 py-1 tabular-nums text-right">
                          {r.v != null ? formatValue(r.v) : (r.vt ?? "—")}
                        </td>
                        <td className="px-2 py-1 whitespace-nowrap text-muted-foreground">{r.engineering_unit ?? "—"}</td>
                        <td className="px-2 py-1 tabular-nums text-right text-muted-foreground">{r.st ?? "—"}</td>
                        <td className="px-2 py-1 whitespace-nowrap">
                          <QualityCell q={r.st_class} />
                        </td>
                        <td className="px-2 py-1 whitespace-nowrap text-muted-foreground">{r.device_name}</td>
                        <td className="px-2 py-1 whitespace-nowrap text-muted-foreground">{r.protocol ?? "—"}</td>
                        <td className="px-2 py-1 whitespace-nowrap text-muted-foreground">{r.channel_name}</td>
                        <td className="px-2 py-1 whitespace-nowrap text-muted-foreground">{r.register_block_name ?? "—"}</td>
                        <td className="px-2 py-1 tabular-nums text-right text-muted-foreground">{r.address ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-between mt-2 text-xs">
                  <span className="text-muted-foreground tabular-nums">
                    Page {page + 1} of {totalPages} · rows {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, displayedRows.length).toLocaleString()} of {displayedRows.length.toLocaleString()}
                  </span>
                  <div className="flex items-center gap-1">
                    <Button variant="outline" size="sm" className="h-7 text-xs"
                      disabled={page === 0}
                      onClick={() => setPage(0)}>« First</Button>
                    <Button variant="outline" size="sm" className="h-7 text-xs"
                      disabled={page === 0}
                      onClick={() => setPage((p) => p - 1)}>‹ Prev</Button>
                    <Button variant="outline" size="sm" className="h-7 text-xs"
                      disabled={page >= totalPages - 1}
                      onClick={() => setPage((p) => p + 1)}>Next ›</Button>
                    <Button variant="outline" size="sm" className="h-7 text-xs"
                      disabled={page >= totalPages - 1}
                      onClick={() => setPage(totalPages - 1)}>Last »</Button>
                  </div>
                </div>
              )}
            </>
          )}

          {/* Distinct empty-state messages so the operator understands
              whether the window has no data at all, or the focus filter
              has hidden everything. */}
          {query.data && displayedRows.length === 0 && focusTagId == null && (
            <p className="text-xs text-muted-foreground py-2">
              No raw rows in this window.
            </p>
          )}
          {query.data && displayedRows.length === 0 && focusTagId != null && (
            <p className="text-xs text-muted-foreground py-2">
              No raw rows for <strong>{focusTagName}</strong> in this window.
              {onClearFocus && (
                <button
                  type="button"
                  onClick={onClearFocus}
                  className="ml-2 underline hover:text-foreground"
                >
                  Clear filter
                </button>
              )}
            </p>
          )}
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function SortableTh({
  label, sortKey, cur, dir, onClick, align = "left",
}: {
  label: string;
  sortKey: SortKey;
  cur: SortKey;
  dir: "asc" | "desc";
  onClick: (k: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = cur === sortKey;
  return (
    <th
      className={`px-2 py-1.5 font-medium whitespace-nowrap cursor-pointer select-none hover:text-foreground ${align === "right" ? "text-right" : "text-left"}`}
      onClick={() => onClick(sortKey)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && (
          dir === "asc"
            ? <ChevronUp className="h-3 w-3" />
            : <ChevronDown className="h-3 w-3" />
        )}
      </span>
    </th>
  );
}

function QualityCell({ q }: { q: string | null }) {
  if (q === "good") {
    return <span className="text-emerald-700 font-semibold text-[10px] uppercase">good</span>;
  }
  if (q === "uncertain") {
    return <span className="text-amber-700 font-semibold text-[10px] uppercase">uncertain</span>;
  }
  if (q === "bad") {
    return <span className="text-red-700 font-semibold text-[10px] uppercase">bad</span>;
  }
  return <span className="text-muted-foreground">—</span>;
}

function formatValue(v: number): string {
  const abs = Math.abs(v);
  let str: string;
  if (abs >= 100)    str = v.toFixed(2);
  else if (abs >= 1) str = v.toFixed(3);
  else                str = v.toFixed(4);
  return str.replace(/\.?0+$/, "");
}

function cmp(a: RawTableRow, b: RawTableRow, key: SortKey, dir: "asc" | "desc"): number {
  let av: string | number | null = (a as any)[key];
  let bv: string | number | null = (b as any)[key];
  // Map "v" to numeric value, falling back to text for non-numeric tags
  if (key === "v") {
    av = a.v != null ? a.v : a.vt;
    bv = b.v != null ? b.v : b.vt;
  }
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  const cmpRes = typeof av === "number" && typeof bv === "number"
    ? av - bv
    : String(av).localeCompare(String(bv));
  return dir === "asc" ? cmpRes : -cmpRes;
}

function toCsv(rows: RawTableRow[], formatDateTime: (t: string) => string): string {
  const headers = [
    "timestamp_local", "timestamp_utc", "tag_name", "value", "value_text",
    "engineering_unit", "st", "st_class", "device", "protocol",
    "channel", "block", "address", "data_type", "source",
  ];
  const lines = [headers.join(",")];
  for (const r of rows) {
    const cells = [
      formatDateTime(r.t),
      r.t,
      r.tag_name,
      r.v ?? "",
      r.vt ?? "",
      r.engineering_unit ?? "",
      r.st ?? "",
      r.st_class ?? "",
      r.device_name,
      r.protocol ?? "",
      r.channel_name,
      r.register_block_name ?? "",
      r.address ?? "",
      r.data_type,
      r.source ?? "",
    ];
    // Quote cells that contain commas, quotes, or newlines.
    lines.push(cells.map(escapeCsv).join(","));
  }
  return lines.join("\n");
}

function escapeCsv(v: string | number): string {
  const s = String(v);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}
