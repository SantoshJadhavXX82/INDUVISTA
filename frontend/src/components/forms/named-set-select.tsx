/**
 * NamedSetSelect — assigns a named_set to a tag.
 *
 * Filters out boolean-only sets when the tag's data type is int (and vice
 * versa), and shows a small preview of the first 3 values inside each
 * option so the user can confirm the set looks right.
 *
 * Phase 8.3
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, X, Search, Check, ExternalLink } from "lucide-react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { NamedSet } from "@/types/api";

export function NamedSetSelect({
  value,
  onChange,
  dataType,
  disabled,
}: {
  value: number | null;
  onChange: (id: number | null) => void;
  /** Tag's data type — used to gently filter the dropdown */
  dataType?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  // Fetch every set including values, so we can render previews + filter by
  // value count without a second round trip.
  const setsQuery = useQuery<NamedSet[]>({
    queryKey: ["named-sets", "with-values"],
    queryFn: () => api.get("/named-sets?enabled=true&include_values=true"),
    staleTime: 60_000,
  });

  const isBoolTag = dataType === "bool";

  // Filter and sort: prefer 2-value sets for bool tags, prefer multi-value
  // sets for int tags. Search filters by name and description.
  const filtered = useMemo(() => {
    const all = setsQuery.data ?? [];
    const q = search.trim().toLowerCase();
    return all
      .filter((s) => {
        if (q) {
          const haystack = `${s.name} ${s.description ?? ""}`.toLowerCase();
          if (!haystack.includes(q)) return false;
        }
        return true;
      })
      .sort((a, b) => {
        if (isBoolTag) {
          // Prefer 2-value sets
          const aBool = a.value_count === 2 ? 0 : 1;
          const bBool = b.value_count === 2 ? 0 : 1;
          if (aBool !== bBool) return aBool - bBool;
        } else {
          // Prefer multi-value sets
          const aMulti = a.value_count >= 3 ? 0 : 1;
          const bMulti = b.value_count >= 3 ? 0 : 1;
          if (aMulti !== bMulti) return aMulti - bMulti;
        }
        return a.name.localeCompare(b.name);
      });
  }, [setsQuery.data, search, isBoolTag]);

  const selected = useMemo(
    () => (value ? setsQuery.data?.find((s) => s.id === value) : null) ?? null,
    [value, setsQuery.data],
  );

  function selectSet(s: NamedSet) {
    onChange(s.id);
    setOpen(false);
    setSearch("");
  }

  function clearSelection() {
    onChange(null);
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className={cn(
          "w-full flex items-center justify-between rounded-md border bg-background",
          "px-3 py-2 text-sm h-9 hover:border-foreground/30 disabled:opacity-50",
          !selected && "text-muted-foreground",
        )}
      >
        <span className="truncate text-left">
          {selected ? (
            <>
              <span className="font-mono text-xs">{selected.name}</span>
              <span className="ml-2 text-muted-foreground text-xs">
                ({selected.value_count} values)
              </span>
            </>
          ) : (
            "No enumeration (raw values)"
          )}
        </span>
        <div className="flex items-center gap-1 shrink-0">
          {selected && (
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => {
                e.stopPropagation();
                clearSelection();
              }}
              className="text-muted-foreground hover:text-foreground p-0.5"
              title="Clear — fall back to raw integer values"
            >
              <X className="h-3.5 w-3.5" />
            </span>
          )}
          <ChevronDown
            className={cn(
              "h-4 w-4 text-muted-foreground transition",
              open && "rotate-180",
            )}
          />
        </div>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full max-h-96 overflow-hidden rounded-md border bg-popover shadow-md flex flex-col">
          <div className="p-2 border-b">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search enumerations…"
                className="pl-7 h-8 text-sm"
                autoFocus
              />
            </div>
          </div>

          <div className="flex-1 overflow-auto py-1">
            {setsQuery.isLoading && (
              <div className="px-3 py-2 text-xs text-muted-foreground">Loading…</div>
            )}
            {!setsQuery.isLoading && filtered.length === 0 && (
              <div className="px-3 py-2 text-xs text-muted-foreground">
                No enumerations match "{search}"
              </div>
            )}
            {filtered.map((s) => {
              const preview = s.values
                .slice(0, 3)
                .map((v) => `${v.raw_value}=${v.display_text}`)
                .join(", ");
              const more = s.values.length > 3 ? `, +${s.values.length - 3} more` : "";
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => selectSet(s)}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-accent"
                >
                  <div className="flex items-center gap-2">
                    {value === s.id && <Check className="h-3.5 w-3.5" />}
                    <span className={cn(
                      "font-mono text-xs font-medium",
                      value !== s.id && "ml-5",
                    )}>
                      {s.name}
                    </span>
                    <span className="text-[10px] uppercase tracking-wide bg-secondary text-secondary-foreground px-1.5 py-0.5 rounded">
                      {s.value_count}v
                    </span>
                  </div>
                  {s.description && (
                    <div className="ml-5 text-xs text-muted-foreground mt-0.5">
                      {s.description}
                    </div>
                  )}
                  <div className="ml-5 text-[11px] text-muted-foreground font-mono mt-0.5">
                    {preview}{more}
                  </div>
                </button>
              );
            })}
          </div>

          <div className="border-t bg-secondary/40">
            <a
              href="/global/named-sets"
              target="_blank"
              rel="noreferrer"
              className="w-full text-left px-3 py-2 text-xs hover:bg-accent flex items-center gap-2"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              <span>Manage enumerations…</span>
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
