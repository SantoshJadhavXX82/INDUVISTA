/**
 * Phase 11 — DevicePicker: searchable, health-aware device selector.
 *
 * Designed as a drop-in replacement for DeviceTabs when device counts grow
 * beyond ~5. DeviceTabs is fine for 2-3 devices; once you have 10, the
 * horizontal scrolling tabs become a usability problem (no overview, slow
 * to find, keyboard-hostile).
 *
 * Affordances:
 *   • Single button shows the currently-selected device + tag count + health dot
 *   • Click opens a popover with a search input + scrollable list
 *   • Type any substring to filter device names instantly
 *   • Each row shows: health dot · name · tag count
 *   • "All devices" pinned at top, always visible
 *   • Keyboard: arrow keys to move, Enter to select, Esc to close
 *   • Recent devices remembered in localStorage so common picks float up
 *
 * The health overlay is best-effort: if the parent passes deviceHealth,
 * we show it; otherwise we leave the dots out and the rest still works.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

type Device = { id: number; name: string };
type Health = "good" | "stale" | "error" | "unknown";

type Props = {
  devices: Device[];
  value: number | null;                          // null = "All"
  onChange: (deviceId: number | null) => void;
  counts?: Record<number | "all", number>;
  /** Optional per-device health for the colored dot. Omit → no dots. */
  deviceHealth?: Record<number, Health>;
  className?: string;
};

const RECENTS_KEY = "induvista-device-picker-recents";
const MAX_RECENTS = 3;

function readRecents(): number[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((n) => typeof n === "number") : [];
  } catch {
    return [];
  }
}

function pushRecent(id: number) {
  const r = readRecents().filter((x) => x !== id);
  r.unshift(id);
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(r.slice(0, MAX_RECENTS)));
  } catch {
    // localStorage can fail in private mode; that's fine, we just lose recency
  }
}

export function DevicePicker({
  devices, value, onChange, counts, deviceHealth, className,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const selected = value === null ? null : devices.find((d) => d.id === value) ?? null;
  const selectedHealth =
    selected && deviceHealth ? deviceHealth[selected.id] : undefined;
  const selectedCount = value === null
    ? counts?.all
    : (value != null ? counts?.[value] : undefined);

  // Filter + sort: matching devices, with recents floated to top, "All" pinned
  const recents = useMemo(() => readRecents(), [open]);   // re-read when popover opens
  const filteredDevices = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matching = devices.filter((d) =>
      q === "" || d.name.toLowerCase().includes(q),
    );
    if (q !== "") return matching;
    // No query — sort recents-first
    const recentSet = new Set(recents);
    const inRecents: Device[] = [];
    const others: Device[] = [];
    for (const d of matching) {
      if (recentSet.has(d.id)) inRecents.push(d);
      else others.push(d);
    }
    inRecents.sort((a, b) => recents.indexOf(a.id) - recents.indexOf(b.id));
    return [...inRecents, ...others];
  }, [devices, query, recents]);

  // Build flat list including "All" for keyboard navigation
  type Row =
    | { kind: "all" }
    | { kind: "device"; device: Device; isRecent: boolean };
  const rows: Row[] = useMemo(() => {
    const recentSet = new Set(recents);
    const list: Row[] = [{ kind: "all" }];
    for (const d of filteredDevices) {
      list.push({
        kind: "device",
        device: d,
        isRecent: query.trim() === "" && recentSet.has(d.id),
      });
    }
    return list;
  }, [filteredDevices, recents, query]);

  // Keep highlight in bounds whenever the visible set changes
  useEffect(() => {
    if (highlight >= rows.length) setHighlight(Math.max(0, rows.length - 1));
  }, [rows.length, highlight]);

  // Focus the search input when the popover opens
  useEffect(() => {
    if (open) {
      // Small delay so the input exists in the DOM
      setTimeout(() => inputRef.current?.focus(), 0);
      setQuery("");
      setHighlight(0);
    }
  }, [open]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (
        popoverRef.current && !popoverRef.current.contains(e.target as Node) &&
        triggerRef.current && !triggerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  function selectRow(idx: number) {
    const row = rows[idx];
    if (!row) return;
    if (row.kind === "all") {
      onChange(null);
    } else {
      onChange(row.device.id);
      pushRecent(row.device.id);
    }
    setOpen(false);
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(rows.length - 1, h + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(0, h - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      selectRow(highlight);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    }
  }

  return (
    <div className={cn("relative inline-block", className)}>
      <Button
        type="button"
        variant="outline"
        ref={triggerRef}
        onClick={() => setOpen((o) => !o)}
        className="min-w-[240px] justify-between gap-2"
      >
        <span className="flex items-center gap-2 truncate">
          {selectedHealth && <HealthDot state={selectedHealth} />}
          <span className="font-medium truncate">
            {selected ? selected.name : "All devices"}
          </span>
          {selectedCount !== undefined && (
            <span className="text-[10px] tabular-nums text-muted-foreground rounded bg-secondary px-1.5 py-0.5">
              {selectedCount}
            </span>
          )}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </Button>

      {open && (
        <div
          ref={popoverRef}
          className="absolute left-0 top-full z-50 mt-1 w-[320px] rounded-md border bg-popover shadow-md"
          onKeyDown={handleKey}
        >
          {/* Search row */}
          <div className="flex items-center gap-2 border-b px-2 py-2">
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => { setQuery(e.target.value); setHighlight(0); }}
              placeholder="Filter devices…"
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {filteredDevices.length}/{devices.length}
            </span>
          </div>

          {/* Rows */}
          <div className="max-h-[320px] overflow-y-auto py-1">
            {rows.length === 0 ? (
              <div className="px-3 py-4 text-center text-xs text-muted-foreground">
                No devices match "{query}"
              </div>
            ) : (
              rows.map((row, idx) => (
                <DeviceRow
                  key={row.kind === "all" ? "all" : row.device.id}
                  row={row}
                  selected={
                    (row.kind === "all" && value === null) ||
                    (row.kind === "device" && row.device.id === value)
                  }
                  highlighted={idx === highlight}
                  onMouseEnter={() => setHighlight(idx)}
                  onClick={() => selectRow(idx)}
                  count={
                    row.kind === "all"
                      ? counts?.all
                      : counts?.[row.device.id]
                  }
                  health={
                    row.kind === "device" && deviceHealth
                      ? deviceHealth[row.device.id]
                      : undefined
                  }
                />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function DeviceRow({
  row, selected, highlighted, onClick, onMouseEnter, count, health,
}: {
  row: { kind: "all" } | { kind: "device"; device: Device; isRecent: boolean };
  selected: boolean;
  highlighted: boolean;
  onClick: () => void;
  onMouseEnter: () => void;
  count?: number;
  health?: Health;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      className={cn(
        "flex w-full items-center gap-2 px-3 py-1.5 text-sm text-left",
        highlighted && "bg-accent",
      )}
    >
      <span className="flex w-4 shrink-0 justify-center">
        {selected && <Check className="h-3.5 w-3.5" />}
      </span>
      {health && <HealthDot state={health} />}
      {row.kind === "all" ? (
        <span className="font-medium">All devices</span>
      ) : (
        <>
          <span className="truncate">{row.device.name}</span>
          {row.isRecent && (
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              recent
            </span>
          )}
        </>
      )}
      <span className="ml-auto text-[10px] tabular-nums text-muted-foreground">
        {count ?? "—"}
      </span>
    </button>
  );
}

function HealthDot({ state }: { state: Health }) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full",
        state === "good" && "bg-green-500",
        state === "stale" && "bg-amber-400",
        state === "error" && "bg-red-500",
        state === "unknown" && "bg-gray-300",
      )}
    />
  );
}
