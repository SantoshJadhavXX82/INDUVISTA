/**
 * OPC UA Sources page — Phase OPC-web.3.
 *
 * Lists all configured OPC sources, derives a Live/Stale/Idle/Disabled
 * state badge from each source's last_sample_at, and exposes the
 * action affordances (Add, Edit, Mappings, Disable/Enable, Delete).
 *
 * State derivation:
 *
 *                  ┌─────────────────────────────────────┐
 *                  │ is_enabled?                          │
 *                  │       │                              │
 *                  │   No  │  Yes                         │
 *                  │       │                              │
 *                  │   ●Disabled                          │
 *                  │ (gray)│                              │
 *                  │       │  last_sample_at?              │
 *                  │       │       │                       │
 *                  │       │   None│  Within…              │
 *                  │       │       │                       │
 *                  │       │   ●Idle (no samples ever)     │
 *                  │       │ (gray)│                       │
 *                  │       │       │  ≤30s: ●Live (green)  │
 *                  │       │       │  ≤5m:  ●Stale (amber) │
 *                  │       │       │  >5m:  ●Lost  (red)   │
 *                  └─────────────────────────────────────┘
 *
 * The page polls /api/opc-sources every 5s so the badge state
 * refreshes naturally without manual reload.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Pencil, ListTree, Power, Trash2, RefreshCw, Network, AlertTriangle,
} from "lucide-react";

import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { OpcSourceResponse } from "@/types/api";

import {
  CreateOpcSourceModal, OPC_SOURCES_QUERY_KEY,
} from "@/components/opc/CreateOpcSourceModal";
import { OpcMappingsDrawer } from "@/components/opc/OpcMappingsDrawer";


/** Derived liveness category, from is_enabled + last_sample_at. */
type LiveState = "disabled" | "idle" | "live" | "stale" | "lost";

interface StateInfo {
  state: LiveState;
  label: string;
  /** Tailwind classes for the dot color. */
  dotCls: string;
  /** Tailwind classes for the wrapper text+bg. */
  pillCls: string;
}

function deriveState(src: OpcSourceResponse): StateInfo {
  if (!src.is_enabled) {
    return {
      state: "disabled",
      label: "Disabled",
      dotCls: "bg-slate-400",
      pillCls: "text-slate-600 bg-slate-100 border-slate-300",
    };
  }
  if (!src.last_sample_at) {
    return {
      state: "idle",
      label: "Idle",
      dotCls: "bg-slate-400",
      pillCls: "text-slate-600 bg-slate-100 border-slate-300",
    };
  }
  const ageSec = (Date.now() - new Date(src.last_sample_at).getTime()) / 1000;
  if (ageSec <= 30) {
    return {
      state: "live",
      label: "Live",
      dotCls: "bg-emerald-500 animate-pulse",
      pillCls: "text-emerald-800 bg-emerald-50 border-emerald-300",
    };
  }
  if (ageSec <= 5 * 60) {
    return {
      state: "stale",
      label: "Stale",
      dotCls: "bg-amber-500",
      pillCls: "text-amber-800 bg-amber-50 border-amber-300",
    };
  }
  return {
    state: "lost",
    label: "Lost",
    dotCls: "bg-red-500",
    pillCls: "text-red-800 bg-red-50 border-red-300",
  };
}


function formatAge(iso: string | null): string {
  if (!iso) return "never";
  const ageSec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (ageSec < 0) return "just now";
  if (ageSec < 60) return `${Math.floor(ageSec)}s ago`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ago`;
  if (ageSec < 86400) return `${Math.floor(ageSec / 3600)}h ago`;
  return `${Math.floor(ageSec / 86400)}d ago`;
}


export default function OpcSources() {
  const qc = useQueryClient();

  const sourcesQuery = useQuery({
    queryKey: OPC_SOURCES_QUERY_KEY,
    queryFn: () => api.get<OpcSourceResponse[]>("/opc-sources"),
    refetchInterval: 5_000,
    staleTime: 0,
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<OpcSourceResponse | null>(null);
  const [mappingsTarget, setMappingsTarget] = useState<OpcSourceResponse | null>(null);
  const [pendingDelete, setPendingDelete] = useState<OpcSourceResponse | null>(null);

  const toggleMutation = useMutation({
    mutationFn: (src: OpcSourceResponse) =>
      api.patch<OpcSourceResponse>(`/opc-sources/${src.id}`, {
        is_enabled: !src.is_enabled,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: OPC_SOURCES_QUERY_KEY }),
  });

  const deleteMutation = useMutation({
    mutationFn: (src: OpcSourceResponse) => api.delete(`/opc-sources/${src.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: OPC_SOURCES_QUERY_KEY });
      setPendingDelete(null);
    },
  });

  const sources = sourcesQuery.data ?? [];
  const summary = useMemo(() => {
    const totals = { live: 0, stale: 0, lost: 0, idle: 0, disabled: 0 };
    for (const s of sources) {
      totals[deriveState(s).state]++;
    }
    return totals;
  }, [sources]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="OPC UA Sources"
        subtitle={
          <span>
            {sources.length} configured
            {sources.length > 0 && (
              <>
                {" · "}
                {summary.live > 0 && <span className="text-emerald-700">{summary.live} live</span>}
                {summary.stale > 0 && <span className="text-amber-700">{summary.live > 0 && ", "}{summary.stale} stale</span>}
                {summary.lost > 0 && <span className="text-red-700">{(summary.live + summary.stale) > 0 && ", "}{summary.lost} lost</span>}
                {summary.idle > 0 && <span className="text-slate-600">{(summary.live + summary.stale + summary.lost) > 0 && ", "}{summary.idle} idle</span>}
                {summary.disabled > 0 && <span className="text-slate-500">{(summary.live + summary.stale + summary.lost + summary.idle) > 0 && ", "}{summary.disabled} disabled</span>}
              </>
            )}
          </span>
        }
        actions={
          <>
            <button
              type="button"
              onClick={() => sourcesQuery.refetch()}
              className="text-xs px-2 py-1.5 rounded border border-border
                         hover:bg-secondary inline-flex items-center gap-1.5"
              title="Refresh now"
            >
              <RefreshCw className={cn(
                "h-3 w-3",
                sourcesQuery.isFetching && "animate-spin",
              )} />
              Refresh
            </button>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground
                         hover:bg-primary/90 inline-flex items-center gap-1.5"
            >
              <Plus className="h-3 w-3" />
              Add OPC Source
            </button>
          </>
        }
      />

      {sourcesQuery.isLoading && (
        <SectionCard title="">
          <div className="text-xs text-muted-foreground py-8 text-center">
            Loading sources…
          </div>
        </SectionCard>
      )}

      {sourcesQuery.isError && (
        <SectionCard title="">
          <div className="text-xs text-destructive py-4 flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <div>
              Failed to load OPC sources. Verify the backend is running
              and the migration completed:{" "}
              <code className="font-mono">docker compose exec backend alembic current</code>
            </div>
          </div>
        </SectionCard>
      )}

      {!sourcesQuery.isLoading && !sourcesQuery.isError && sources.length === 0 && (
        <SectionCard title="No sources yet">
          <div className="py-8 text-center space-y-3">
            <Network className="h-8 w-8 text-muted-foreground mx-auto" />
            <p className="text-sm">
              No OPC UA sources configured.
            </p>
            <p className="text-xs text-muted-foreground max-w-md mx-auto leading-relaxed">
              Add a source to start subscribing to plant OPC UA endpoints.
              The backend opc_worker will connect, hold a subscription,
              and persist samples into <code className="font-mono">tag_values</code>.
            </p>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground
                         hover:bg-primary/90 inline-flex items-center gap-1.5"
            >
              <Plus className="h-3 w-3" />
              Add your first OPC Source
            </button>
          </div>
        </SectionCard>
      )}

      {sources.length > 0 && (
        <SectionCard title="">
          <div className="divide-y divide-border">
            {sources.map((src) => {
              const info = deriveState(src);
              return (
                <div key={src.id} className="px-3 py-3 flex items-start gap-3 hover:bg-secondary/20">
                  {/* State + name */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span
                        className={cn(
                          "text-[10px] uppercase tracking-wide font-medium border rounded px-1.5 py-0.5 inline-flex items-center gap-1",
                          info.pillCls,
                        )}
                      >
                        <span className={cn("h-1.5 w-1.5 rounded-full", info.dotCls)} />
                        {info.label}
                      </span>
                      <span className="text-sm font-medium">{src.name}</span>
                      <span className="text-[10px] text-muted-foreground">
                        {src.mapping_count} {src.mapping_count === 1 ? "tag" : "tags"}
                      </span>
                    </div>
                    {src.description && (
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {src.description}
                      </p>
                    )}
                    <div className="text-[10px] text-muted-foreground mt-1 font-mono truncate">
                      {src.endpoint}
                    </div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">
                      Last sample: <span className="text-foreground">{formatAge(src.last_sample_at)}</span>
                      {" · "}
                      Publishing: {src.publishing_interval_ms}ms
                      {src.security_policy !== "None" && (
                        <> {" · "} <span className="text-amber-700">{src.security_policy}</span></>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      type="button"
                      onClick={() => setMappingsTarget(src)}
                      className="text-xs px-2 py-1 rounded border border-border
                                 hover:bg-secondary inline-flex items-center gap-1"
                      title="View / edit mappings"
                    >
                      <ListTree className="h-3 w-3" />
                      Mappings
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditTarget(src)}
                      className="h-7 w-7 inline-flex items-center justify-center rounded
                                 text-muted-foreground hover:bg-secondary hover:text-foreground"
                      title="Edit source"
                      aria-label="Edit source"
                    >
                      <Pencil className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleMutation.mutate(src)}
                      disabled={toggleMutation.isPending}
                      className={cn(
                        "h-7 w-7 inline-flex items-center justify-center rounded",
                        "hover:bg-secondary disabled:opacity-30",
                        src.is_enabled ? "text-amber-700" : "text-emerald-700",
                      )}
                      title={src.is_enabled ? "Disable source" : "Enable source"}
                      aria-label={src.is_enabled ? "Disable source" : "Enable source"}
                    >
                      <Power className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setPendingDelete(src)}
                      className="h-7 w-7 inline-flex items-center justify-center rounded
                                 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      title="Delete source (drops mappings + tags + history)"
                      aria-label="Delete source"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </SectionCard>
      )}

      <p className="text-[10px] text-muted-foreground italic px-1">
        Changes to sources or mappings require a worker restart to take effect:{" "}
        <code className="font-mono">docker compose restart opc_worker</code>.
        Hot-reload is on the roadmap (Phase OPC-web.2.1).
      </p>

      {/* Modals + drawers */}
      <CreateOpcSourceModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
      <CreateOpcSourceModal
        open={!!editTarget}
        existingSource={editTarget}
        onClose={() => setEditTarget(null)}
      />
      <OpcMappingsDrawer
        open={!!mappingsTarget}
        source={mappingsTarget}
        onClose={() => setMappingsTarget(null)}
      />
      <ConfirmDialog
        open={!!pendingDelete}
        title="Delete OPC source?"
        description={
          pendingDelete ? (
            <>
              This will delete <b>{pendingDelete.name}</b> along with its
              synthetic channel and device, all{" "}
              <b>{pendingDelete.mapping_count}</b> mapped tags, and{" "}
              <span className="text-destructive font-medium">
                all historical samples for those tags
              </span>
              . To stop sampling without losing data, disable the source
              instead.
            </>
          ) : null
        }
        confirmLabel="Delete source + history"
        severity="destructive"
        requireTextMatch={pendingDelete?.name}
        busy={deleteMutation.isPending}
        onConfirm={() => pendingDelete && deleteMutation.mutate(pendingDelete)}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
