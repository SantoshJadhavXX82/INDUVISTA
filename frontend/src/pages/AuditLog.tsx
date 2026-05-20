/**
 * Phase 16.0g - Audit Log viewer.
 *
 * Read-only list of every state-changing action performed via the API.
 * Filterable by action, target, status, date range. Polls every 10s so
 * fresh events appear without manual refresh.
 */
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Shield, RefreshCw, AlertTriangle, ChevronDown, ChevronRight,
  CheckCircle2, XCircle, Ban,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";


// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AuditEvent {
  id: number;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  actor_ip: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  target_label: string | null;
  summary: string | null;
  details: unknown;
  status: string;
  error_message: string | null;
  correlation_id: string | null;
}

interface AuditListResponse {
  total: number;
  limit: number;
  offset: number;
  events: AuditEvent[];
}


// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

interface Filters {
  action: string;     // prefix or exact ("calc.", "calc.delete", "")
  status: string;     // "" | "success" | "denied" | "error"
  hours: number;      // hours back from now
}

function useAuditEvents(filters: Filters, limit: number, offset: number) {
  const since = useMemo(
    () => new Date(Date.now() - filters.hours * 3600_000).toISOString(),
    [filters.hours]
  );
  const params = new URLSearchParams();
  if (filters.action) params.set("action", filters.action);
  if (filters.status) params.set("status", filters.status);
  params.set("since", since);
  params.set("limit", String(limit));
  params.set("offset", String(offset));

  return useQuery<AuditListResponse>({
    queryKey: ["audit-log", filters, limit, offset],
    queryFn: async () => {
      const res = await fetch(`/api/audit-log?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    refetchInterval: 10_000,
    staleTime: 5_000,
  });
}

function useDistinctActions() {
  return useQuery<string[]>({
    queryKey: ["audit-log-actions"],
    queryFn: async () => {
      const res = await fetch("/api/audit-log/actions");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    staleTime: 30_000,
  });
}


// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const STATUS_STYLES: Record<string, { cls: string; icon: React.ReactNode }> = {
  success: { cls: "bg-emerald-50 text-emerald-800 border-emerald-300", icon: <CheckCircle2 className="h-2.5 w-2.5" /> },
  denied:  { cls: "bg-amber-50 text-amber-800 border-amber-300",       icon: <Ban className="h-2.5 w-2.5" /> },
  error:   { cls: "bg-red-50 text-red-800 border-red-300",             icon: <XCircle className="h-2.5 w-2.5" /> },
};

const PAGE_SIZE = 50;

export default function AuditLog() {
  const [filterAction, setFilterAction] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [filterHours, setFilterHours] = useState<number>(24);
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const filters: Filters = { action: filterAction, status: filterStatus, hours: filterHours };
  const events = useAuditEvents(filters, PAGE_SIZE, page * PAGE_SIZE);
  const actions = useDistinctActions();

  const total = events.data?.total ?? 0;
  const pageCount = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        title="Audit log"
        subtitle={`${total.toLocaleString()} events recorded`}
        actions={
          <button
            type="button"
            onClick={() => events.refetch()}
            disabled={events.isFetching}
            className="h-7 inline-flex items-center gap-1 text-xs px-2 rounded border border-border hover:bg-secondary disabled:opacity-30"
            style={{ borderColor: "var(--separator)" }}
          >
            <RefreshCw className={`h-3 w-3 ${events.isFetching ? "animate-spin" : ""}`} />
            Refresh
          </button>
        }
      />
      <Card>
        <CardContent className="p-3">
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2 mb-3 text-xs">
            <select
              className="h-7 text-xs bg-card border border-border rounded px-2"
              value={filterAction}
              onChange={(e) => { setFilterAction(e.target.value); setPage(0); }}
            >
              <option value="">All actions</option>
              {(actions.data ?? []).map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
            <select
              className="h-7 text-xs bg-card border border-border rounded px-2"
              value={filterStatus}
              onChange={(e) => { setFilterStatus(e.target.value); setPage(0); }}
            >
              <option value="">All statuses</option>
              <option value="success">success</option>
              <option value="denied">denied</option>
              <option value="error">error</option>
            </select>
            <select
              className="h-7 text-xs bg-card border border-border rounded px-2"
              value={filterHours}
              onChange={(e) => { setFilterHours(Number(e.target.value)); setPage(0); }}
            >
              <option value={1}>last 1 hour</option>
              <option value={6}>last 6 hours</option>
              <option value={24}>last 24 hours</option>
              <option value={24 * 7}>last 7 days</option>
              <option value={24 * 30}>last 30 days</option>
              <option value={24 * 365}>last year</option>
            </select>
          </div>

          {events.isLoading && (
            <div className="text-xs text-muted-foreground italic py-4 text-center">
              <RefreshCw className="inline h-3 w-3 animate-spin mr-1" />
              Loading...
            </div>
          )}

          {events.isError && (
            <div className="flex items-start gap-2 text-xs text-destructive bg-destructive/10 border border-destructive/30 rounded p-2">
              <AlertTriangle className="h-3 w-3 flex-shrink-0 mt-0.5" />
              <span>{String(events.error)}</span>
            </div>
          )}

          {events.data && events.data.events.length === 0 && (
            <div className="text-xs text-muted-foreground italic py-4 text-center">
              No events in this window. Try expanding the date range.
            </div>
          )}

          {events.data && events.data.events.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-muted-foreground text-[10px] uppercase tracking-wider">
                    <th className="w-4 p-0"></th>
                    <th className="text-left px-2 py-2 font-medium">When</th>
                    <th className="text-left px-3 py-2 font-medium">Action</th>
                    <th className="text-left px-3 py-2 font-medium">Target</th>
                    <th className="text-left px-3 py-2 font-medium">Summary</th>
                    <th className="text-center px-3 py-2 font-medium">Status</th>
                    <th className="text-left px-3 py-2 font-medium">Actor IP</th>
                  </tr>
                </thead>
                <tbody>
                  {events.data.events.map((ev) => (
                    <AuditRow
                      key={ev.id}
                      ev={ev}
                      expanded={expandedId === ev.id}
                      onToggle={() => setExpandedId(expandedId === ev.id ? null : ev.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {pageCount > 1 && (
            <div className="flex items-center justify-between mt-3 text-xs text-muted-foreground">
              <span>
                Page {page + 1} of {pageCount}
              </span>
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={() => setPage(Math.max(0, page - 1))}
                  disabled={page === 0}
                  className="px-2 py-1 rounded border border-border hover:bg-secondary disabled:opacity-30"
                >
                  Previous
                </button>
                <button
                  type="button"
                  onClick={() => setPage(Math.min(pageCount - 1, page + 1))}
                  disabled={page >= pageCount - 1}
                  className="px-2 py-1 rounded border border-border hover:bg-secondary disabled:opacity-30"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <p className="px-1 text-[11px] text-muted-foreground">
        Audit log is stored in a dedicated database (<code>induvista_audit</code>) with a 1-year
        retention policy via TimescaleDB. Every state-changing API call records here. Read-only.
      </p>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Row + detail panel
// ---------------------------------------------------------------------------

function AuditRow({ ev, expanded, onToggle }: { ev: AuditEvent; expanded: boolean; onToggle: () => void }) {
  const ss = STATUS_STYLES[ev.status] ?? STATUS_STYLES.success;
  const ts = new Date(ev.occurred_at);
  const ago = relativeTime(ev.occurred_at);

  return (
    <>
      <tr
        className="border-t border-border hover:bg-secondary/30 cursor-pointer"
        onClick={onToggle}
      >
        <td className="px-1 text-muted-foreground">
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </td>
        <td className="px-2 py-1.5 text-muted-foreground tabular-nums whitespace-nowrap" title={ts.toLocaleString()}>
          {ago}
        </td>
        <td className="px-3 py-1.5 font-mono text-[11px]">{ev.action}</td>
        <td className="px-3 py-1.5">
          {ev.target_label ? (
            <span>
              {ev.target_label}
              {ev.target_id && <span className="text-muted-foreground ml-1">#{ev.target_id}</span>}
            </span>
          ) : (
            <span className="text-muted-foreground italic">-</span>
          )}
        </td>
        <td className="px-3 py-1.5 text-muted-foreground">
          {ev.summary ?? <span className="italic">-</span>}
        </td>
        <td className="px-3 py-1.5 text-center">
          <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${ss.cls}`}>
            {ss.icon}
            {ev.status}
          </span>
        </td>
        <td className="px-3 py-1.5 text-muted-foreground font-mono text-[11px]">
          {ev.actor_ip ?? "-"}
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-border bg-secondary/15">
          <td colSpan={7} className="p-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Details</div>
                <pre className="text-[11px] font-mono bg-card border border-border rounded p-2 overflow-x-auto max-h-96">
                  {ev.details ? JSON.stringify(ev.details, null, 2) : "(none)"}
                </pre>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Metadata</div>
                <table className="text-xs">
                  <tbody>
                    <tr><td className="pr-3 text-muted-foreground">Event ID</td><td className="tabular-nums">{ev.id}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Occurred at</td><td className="text-[11px]">{ts.toLocaleString()}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Actor type</td><td>{ev.actor_type}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Actor ID</td><td>{ev.actor_id ?? "-"}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Actor IP</td><td className="font-mono">{ev.actor_ip ?? "-"}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Target</td><td>{ev.target_type ?? "-"}{ev.target_id && ` #${ev.target_id}`}</td></tr>
                    <tr><td className="pr-3 text-muted-foreground">Correlation</td><td className="font-mono text-[10px]">{ev.correlation_id ?? "-"}</td></tr>
                    {ev.error_message && (
                      <tr><td className="pr-3 text-muted-foreground align-top">Error</td><td className="text-destructive whitespace-pre-wrap">{ev.error_message}</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 0) return "future";
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}
