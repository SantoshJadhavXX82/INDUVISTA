/**
 * Phase 13.4 — Saved trend views.
 *
 * Renders a "Saved" dropdown button. On click, opens a panel with:
 *   - A save form at the top (name input + Save button)
 *   - A scrollable list of existing views below, each clickable to load,
 *     with a delete button on hover
 *
 * The currently active chart configuration is passed in as a prop so the
 * Save action snapshots exactly what the operator is looking at right now.
 * On load, we call back to the parent with the saved config and the parent
 * decides how to apply it (this component is purely presentational + API).
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type {
  TrendView, TrendViewConfig, TrendViewCreate,
} from "@/types/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type SavedViewsProps = {
  currentConfig: TrendViewConfig;
  onLoad: (config: TrendViewConfig) => void;
};

export default function SavedViews({ currentConfig, onLoad }: SavedViewsProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();

  const viewsQuery = useQuery({
    queryKey: ["trend-views"],
    queryFn: () => api.get<TrendView[]>("/trends/views"),
    staleTime: 10_000,
  });

  const saveMutation = useMutation({
    mutationFn: (payload: TrendViewCreate) =>
      api.post<TrendView>("/trends/views", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trend-views"] });
      setName("");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/trends/views/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trend-views"] }),
  });

  // Close dropdown when clicking outside (same pattern as TagPicker).
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const handleSave = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (currentConfig.tag_ids.length === 0) return;
    saveMutation.mutate({
      name: trimmed,
      config: currentConfig,
    });
  };

  const handleLoad = (view: TrendView) => {
    onLoad(view.config);
    setOpen(false);
  };

  const handleDelete = (view: TrendView) => {
    if (confirm(`Delete saved view "${view.name}"?`)) {
      deleteMutation.mutate(view.id);
    }
  };

  const views = viewsQuery.data ?? [];
  const canSave = name.trim().length > 0 && currentConfig.tag_ids.length > 0;

  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => setOpen((v) => !v)}
      >
        <Bookmark className="h-3 w-3" />
        Saved
        {views.length > 0 && (
          <span className="text-muted-foreground">({views.length})</span>
        )}
      </Button>

      {open && (
        <div className="absolute right-0 top-full mt-1 w-[340px] z-50 bg-card border border-border rounded-md shadow-lg">
          {/* Save form */}
          <div className="p-2 border-b border-border">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1.5 px-1">
              Save current view
            </div>
            <div className="flex gap-1">
              <Input
                placeholder="View name…"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSave();
                }}
                className="h-8 text-xs"
                disabled={currentConfig.tag_ids.length === 0}
              />
              <Button
                size="sm"
                className="h-8 px-3 text-xs gap-1"
                onClick={handleSave}
                disabled={!canSave || saveMutation.isPending}
              >
                <Plus className="h-3 w-3" />
                Save
              </Button>
            </div>
            {currentConfig.tag_ids.length === 0 && (
              <p className="text-[10px] text-muted-foreground mt-1 px-1">
                Pick at least one tag first.
              </p>
            )}
            {saveMutation.isError && (
              <p className="text-[10px] text-destructive mt-1 px-1">
                {(saveMutation.error as Error)?.message ?? "Save failed"}
              </p>
            )}
          </div>

          {/* List */}
          <div className="max-h-[320px] overflow-y-auto">
            {viewsQuery.isLoading && (
              <p className="p-3 text-xs text-muted-foreground">Loading…</p>
            )}
            {viewsQuery.isError && (
              <p className="p-3 text-xs text-destructive">
                Failed to load: {(viewsQuery.error as Error)?.message}
              </p>
            )}
            {!viewsQuery.isLoading && views.length === 0 && (
              <p className="p-3 text-xs text-muted-foreground text-center">
                No saved views yet.
              </p>
            )}
            {views.map((v) => (
              <div
                key={v.id}
                className="px-3 py-2 border-b border-border hover:bg-secondary/40 flex items-center gap-2 group"
              >
                <button
                  type="button"
                  className="flex-1 text-left text-xs min-w-0"
                  onClick={() => handleLoad(v)}
                >
                  <div className="font-medium truncate">{v.name}</div>
                  <div className="text-muted-foreground truncate">
                    {v.config.tag_ids.length} tag
                    {v.config.tag_ids.length === 1 ? "" : "s"}
                    {" · "}
                    {v.config.preset_label ??
                      (v.config.mode === "live"
                        ? `Last ${v.config.preset_minutes}m`
                        : "custom range")}
                    {v.config.mode === "live" && " · Live"}
                  </div>
                </button>
                <button
                  type="button"
                  className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-destructive/10 text-destructive transition-opacity"
                  onClick={() => handleDelete(v)}
                  title="Delete saved view"
                  disabled={deleteMutation.isPending}
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
