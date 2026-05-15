/**
 * Phase 13.2 — Multi-select tag picker for the Trend page.
 *
 * Fetches all enabled tags from /api/trends/tags once, then filters
 * client-side as the user types. The list caps at 500 tags so this is
 * always cheap. Beyond that we'd add server-side search via the `q`
 * query parameter.
 *
 * Returns a button with a popover-style dropdown — no external popover
 * library needed; absolute positioning + outside-click handling does it.
 */
import { useEffect, useRef, useState } from "react";
import { Plus, Search, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { TrendTag } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

type TagPickerProps = {
  selectedIds: number[];
  onChange: (ids: number[]) => void;
  maxTags?: number;
};

export default function TagPicker({ selectedIds, onChange, maxTags = 6 }: TagPickerProps) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);

  // Fetch every enabled tag once. Use the API's max (2000) so even
  // large-site deployments see everything; client-side filter handles
  // search. If a deployment outgrows 2000 enabled tags we'll need
  // server-side search via the `q` query parameter.
  const tagsQuery = useQuery({
    queryKey: ["trend-tags"],
    queryFn: () => api.get<TrendTag[]>("/trends/tags?enabled_only=true&limit=2000"),
    staleTime: 30_000,        // tag list changes rarely
  });

  // Close dropdown when clicking outside.
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

  const tags = tagsQuery.data ?? [];
  const selected = tags.filter((t) => selectedIds.includes(t.id));

  const ql = q.trim().toLowerCase();
  const filtered = ql
    ? tags.filter((t) =>
        t.name.toLowerCase().includes(ql) ||
        t.device_name.toLowerCase().includes(ql) ||
        (t.description?.toLowerCase().includes(ql) ?? false),
      )
    : tags;

  const toggle = (id: number) => {
    if (selectedIds.includes(id)) {
      onChange(selectedIds.filter((x) => x !== id));
    } else if (selectedIds.length < maxTags) {
      onChange([...selectedIds, id]);
    }
  };

  const remove = (id: number) => {
    onChange(selectedIds.filter((x) => x !== id));
  };

  const clearAll = () => onChange([]);

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {/* Selected tag chips — each removable */}
      {selected.map((t, idx) => (
        <Badge
          key={t.id}
          variant="outline"
          className="gap-1.5 pl-1.5 pr-1 py-1 text-xs"
        >
          <span
            className="inline-block w-3 h-3 rounded-sm"
            style={{ backgroundColor: TAG_COLOR_AT(idx) }}
            aria-hidden
          />
          <span className="font-medium">{t.name}</span>
          {t.engineering_unit && (
            <span className="text-muted-foreground">{t.engineering_unit}</span>
          )}
          <button
            type="button"
            className="ml-1 rounded-full hover:bg-secondary/60 p-0.5"
            aria-label={`Remove ${t.name}`}
            onClick={() => remove(t.id)}
          >
            <X className="h-3 w-3" />
          </button>
        </Badge>
      ))}

      {/* Add-tag button + dropdown */}
      <div ref={wrapRef} className="relative">
        <Button
          variant="outline"
          size="sm"
          className="h-7 px-2 text-xs gap-1"
          onClick={() => setOpen((v) => !v)}
          disabled={selectedIds.length >= maxTags}
        >
          <Plus className="h-3 w-3" />
          {selectedIds.length === 0 ? "Add tag" : "Add another"}
        </Button>

        {open && (
          <div
            className="absolute left-0 top-full mt-1 w-[360px] z-50
                       bg-card border border-border rounded-md shadow-lg
                       max-h-[400px] flex flex-col"
          >
            <div className="p-2 border-b border-border">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <Input
                  autoFocus
                  placeholder="Search by tag name, device, description…"
                  className="pl-7 h-8 text-xs"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                />
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              {tagsQuery.isLoading && (
                <p className="p-3 text-xs text-muted-foreground">Loading tags…</p>
              )}
              {tagsQuery.isError && (
                <p className="p-3 text-xs text-destructive">
                  Failed to load tags: {(tagsQuery.error as Error)?.message}
                </p>
              )}
              {!tagsQuery.isLoading && filtered.length === 0 && (
                <p className="p-3 text-xs text-muted-foreground">
                  No tags match “{q}”.
                </p>
              )}
              {filtered.slice(0, 1000).map((t) => {
                const isSelected = selectedIds.includes(t.id);
                const disabled = !isSelected && selectedIds.length >= maxTags;
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => toggle(t.id)}
                    disabled={disabled}
                    className={`w-full px-3 py-2 text-left text-xs border-b border-border
                                hover:bg-secondary/40 flex items-center gap-2
                                ${isSelected ? "bg-secondary/30" : ""}
                                ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="font-medium truncate flex items-center gap-1.5">
                        {t.name}
                        {t.engineering_unit && (
                          <span className="text-muted-foreground font-normal">
                            {t.engineering_unit}
                          </span>
                        )}
                      </div>
                      <div className="text-muted-foreground truncate">
                        {t.device_name} · {t.data_type}
                        {t.current_quality && (
                          <span className={qualityClass(t.current_quality)}>
                            {" · "}{t.current_quality}
                          </span>
                        )}
                      </div>
                    </div>
                    {isSelected && (
                      <span className="text-[10px] text-emerald-700 font-semibold">
                        SELECTED
                      </span>
                    )}
                  </button>
                );
              })}
              {filtered.length > 1000 && (
                <p className="p-2 text-[10px] text-muted-foreground text-center">
                  Showing first 1000 of {filtered.length} matches — refine your search.
                </p>
              )}
            </div>
            {selectedIds.length > 0 && (
              <div className="p-2 border-t border-border flex items-center justify-between text-xs">
                <span className="text-muted-foreground">
                  {selectedIds.length} / {maxTags} selected
                </span>
                <button
                  type="button"
                  className="text-destructive hover:underline"
                  onClick={clearAll}
                >
                  Clear all
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers — mirror TrendChart's palette so the chip swatches match the
// chart line colors exactly.
// ---------------------------------------------------------------------------
const TAG_COLORS = [
  "#14a06e", "#2563eb", "#b45309", "#7c3aed", "#dc2626", "#0d5e6e",
];
export const TAG_COLOR_AT = (idx: number) => TAG_COLORS[idx % TAG_COLORS.length];

function qualityClass(q: string): string {
  if (q === "good") return " text-emerald-700";
  if (q === "uncertain") return " text-amber-700";
  if (q === "bad") return " text-red-700";
  return "";
}
