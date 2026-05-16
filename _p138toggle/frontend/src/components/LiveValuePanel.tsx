/**
 * Phase 13.8 - Live Value Panel (spec section 6.3).
 *
 * Shows, for each selected tag, the *current real-time* values regardless
 * of whether the operator is viewing live or historical trends. The chart
 * may be parked on yesterday's data, but this panel always tells the
 * operator what the plant is doing right now.
 *
 * Per tile, displays the spec fields:
 *   - Tag Name
 *   - Current Value (CV) + Engineering Unit
 *   - ST integer + Quality class chip
 *   - Last update time (formatted by global TimeFormat)
 *   - Device + Protocol (subtle line under name)
 *   - Communication state (derived from staleness of last_update_utc)
 *   - Change indicator (arrow showing direction since previous fetch)
 *
 * Polling cadence:
 *   - Live mode: same interval as the chart's refresh selector
 *   - Historical mode: 30 seconds (panel is still live, just less aggressive)
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Activity, ArrowDown, ArrowRight, ArrowUp, Eye, EyeOff } from "lucide-react";
import { api } from "@/lib/api";
import type { TrendTag } from "@/types/api";
import { useTimeFormat } from "@/lib/timeFormat";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TAG_COLOR_AT } from "@/components/TagPicker";

type LiveValuePanelProps = {
  selectedIds: number[];
  liveMode: boolean;
  refreshIntervalSec: number;
  /** Set of tag IDs whose chart series is currently hidden. Tiles for
   *  these tags render with a "hidden" visual state. Click any tile to
   *  toggle. */
  hiddenTagIds?: Set<number>;
  onToggleHidden?: (tagId: number) => void;
};

// Staleness thresholds in milliseconds. Real plants typically configure
// per-device heartbeat-max-stale, but those values aren't in TrendTag.
// We use generic thresholds; can be made tag-specific later.
const COMM_FRESH_MS  = 10_000;   // < 10s old = fresh
const COMM_SLOW_MS   = 60_000;   // 10s-1m = slow but reachable
const COMM_STALE_MS  = 300_000;  // 1m-5m = stale
// > 5m = offline

type CommState = "fresh" | "slow" | "stale" | "offline";

function commStateFrom(lastUpdateIso: string | null): CommState {
  if (!lastUpdateIso) return "offline";
  const age = Date.now() - new Date(lastUpdateIso).getTime();
  if (age < COMM_FRESH_MS) return "fresh";
  if (age < COMM_SLOW_MS) return "slow";
  if (age < COMM_STALE_MS) return "stale";
  return "offline";
}

function commStateClasses(s: CommState): { bg: string; fg: string; label: string } {
  switch (s) {
    case "fresh":   return { bg: "bg-emerald-50",  fg: "text-emerald-700", label: "FRESH" };
    case "slow":    return { bg: "bg-blue-50",     fg: "text-blue-700",    label: "SLOW" };
    case "stale":   return { bg: "bg-amber-50",    fg: "text-amber-800",   label: "STALE" };
    case "offline": return { bg: "bg-red-50",      fg: "text-red-800",     label: "OFFLINE" };
  }
}

function qualityClasses(q: string | null): { fg: string; label: string } {
  switch (q) {
    case "good":      return { fg: "text-emerald-700", label: "GOOD" };
    case "uncertain": return { fg: "text-amber-700",   label: "UNCERTAIN" };
    case "bad":       return { fg: "text-red-700",     label: "BAD" };
    default:          return { fg: "text-muted-foreground", label: "—" };
  }
}

function formatNum(v: number | null): string {
  if (v == null) return "—";
  const abs = Math.abs(v);
  let str: string;
  if (abs >= 100)    str = v.toFixed(2);
  else if (abs >= 1) str = v.toFixed(3);
  else                str = v.toFixed(4);
  return str.replace(/\.?0+$/, "");
}

function formatRelativeAge(ms: number): string {
  if (ms < 1000) return "now";
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  return `${Math.floor(ms / 3_600_000)}h ago`;
}

export default function LiveValuePanel({
  selectedIds, liveMode, refreshIntervalSec,
  hiddenTagIds, onToggleHidden,
}: LiveValuePanelProps) {
  // Tick local state to refresh "Xs ago" strings even when no new data
  // has arrived. Lightweight - just a state setter every second.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (selectedIds.length === 0) return;
    const t = setInterval(() => setTick((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, [selectedIds.length]);

  const query = useQuery({
    // Separate query key from the static TagPicker fetch so this one
    // polls aggressively without forcing the picker to re-render.
    queryKey: ["live-values"],
    queryFn: () => api.get<TrendTag[]>("/trends/tags?enabled_only=false&limit=2000"),
    refetchInterval: liveMode ? refreshIntervalSec * 1000 : 30_000,
    enabled: selectedIds.length > 0,
    staleTime: 0,
  });

  // Track previous values for change indicators. The arrow lingers for
  // a few seconds visually, but we update the ref immediately so the
  // *next* tick compares against the latest seen value.
  const prevValuesRef = useRef<Map<number, number | null>>(new Map());
  const changesRef    = useRef<Map<number, { dir: "up" | "down" | "same"; at: number }>>(new Map());

  if (selectedIds.length === 0) return null;

  const allTags = query.data ?? [];
  // Preserve the order in which tags were picked - matches chart series order.
  const tags = selectedIds
    .map((id) => allTags.find((t) => t.id === id))
    .filter((t): t is TrendTag => !!t);

  // Diff against previous fetch.
  tags.forEach((t) => {
    const prev = prevValuesRef.current.get(t.id);
    const curr = t.current_value_double;
    if (prev != null && curr != null && prev !== curr) {
      changesRef.current.set(t.id, {
        dir: curr > prev ? "up" : "down",
        at: Date.now(),
      });
    } else if (prev != null && curr != null && prev === curr) {
      // Only set "same" if there hasn't been a recent up/down marker.
      const existing = changesRef.current.get(t.id);
      if (!existing || Date.now() - existing.at > 3000) {
        changesRef.current.set(t.id, { dir: "same", at: Date.now() });
      }
    }
    if (curr != null) prevValuesRef.current.set(t.id, curr);
  });

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <Activity className="h-3.5 w-3.5" />
          Live values
          <span className="text-[10px] text-muted-foreground font-normal">
            {liveMode
              ? `polls every ${refreshIntervalSec}s`
              : "polls every 30s (historical mode)"}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div
          className="grid gap-2"
          style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}
        >
          {tags.map((t, idx) => (
            <Tile
              key={t.id}
              tag={t}
              color={TAG_COLOR_AT(idx)}
              change={changesRef.current.get(t.id)}
              isHidden={hiddenTagIds?.has(t.id) ?? false}
              onToggleHidden={onToggleHidden ? () => onToggleHidden(t.id) : undefined}
            />
          ))}
        </div>
        {query.isError && (
          <p className="text-xs text-destructive mt-2">
            Failed to fetch live values: {(query.error as Error)?.message}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Single-tag tile
// ---------------------------------------------------------------------------

function Tile({
  tag, color, change, isHidden, onToggleHidden,
}: {
  tag: TrendTag;
  color: string;
  change: { dir: "up" | "down" | "same"; at: number } | undefined;
  isHidden: boolean;
  onToggleHidden?: () => void;
}) {
  const { formatTimeShort } = useTimeFormat();
  const comm = commStateFrom(tag.last_update_utc);
  const commStyle = commStateClasses(comm);
  const qual = qualityClasses(tag.current_quality);

  // Visualize value: prefer numeric, fall back to text for string tags.
  const valueDisplay = tag.current_value_double != null
    ? formatNum(tag.current_value_double)
    : (tag.current_value_text ?? "—");

  // Change arrow visible for 4 seconds after a change.
  const showArrow = change && Date.now() - change.at < 4000 && change.dir !== "same";
  const ArrowIcon =
    change?.dir === "up"   ? ArrowUp :
    change?.dir === "down" ? ArrowDown :
    ArrowRight;
  const arrowColor =
    change?.dir === "up"   ? "text-emerald-600" :
    change?.dir === "down" ? "text-orange-600" :
    "text-muted-foreground";

  // Last-update age string
  const ageMs = tag.last_update_utc
    ? Date.now() - new Date(tag.last_update_utc).getTime()
    : null;

  // The whole tile is the click target. Visual hidden state: dimmer,
  // dashed border, strikethrough name, faded color swatch. The tile still
  // updates with live values - the operator just hid it from the chart.
  const interactive = onToggleHidden != null;

  return (
    <div
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onClick={onToggleHidden}
      onKeyDown={interactive ? (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onToggleHidden?.();
        }
      } : undefined}
      title={interactive
        ? (isHidden ? "Click to show on chart" : "Click to hide from chart")
        : undefined}
      className={[
        "rounded-md p-2.5 flex flex-col gap-1.5 transition-colors",
        interactive ? "cursor-pointer select-none" : "",
        isHidden
          ? "bg-secondary/40 border border-dashed border-border opacity-60"
          : "bg-card border border-border hover:border-foreground/30",
      ].join(" ")}
    >
      {/* Row 1: color swatch + name + comm state + eye icon */}
      <div className="flex items-center gap-2">
        <span
          className={`inline-block w-3 h-3 rounded-sm flex-shrink-0 ${isHidden ? "opacity-40" : ""}`}
          style={{ backgroundColor: color }}
          aria-hidden
        />
        <span className={`font-semibold text-xs truncate flex-1 ${isHidden ? "line-through text-muted-foreground" : ""}`}>
          {tag.name}
        </span>
        {interactive && (
          isHidden
            ? <EyeOff className="h-3 w-3 text-muted-foreground flex-shrink-0" aria-label="hidden" />
            : <Eye className="h-3 w-3 text-muted-foreground/40 flex-shrink-0" aria-label="visible" />
        )}
        <span
          className={`text-[9px] font-semibold uppercase tracking-wider rounded-sm px-1.5 py-0 ${commStyle.bg} ${commStyle.fg}`}
          title={`Last update: ${tag.last_update_utc ?? "never"}`}
        >
          {commStyle.label}
        </span>
      </div>

      {/* Row 2: value + EU + change arrow */}
      <div className="flex items-baseline gap-1.5 tabular-nums">
        <span className="text-lg font-semibold leading-none">{valueDisplay}</span>
        {tag.engineering_unit && (
          <span className="text-[10px] text-muted-foreground">{tag.engineering_unit}</span>
        )}
        {showArrow && (
          <ArrowIcon
            className={`h-3.5 w-3.5 ml-auto ${arrowColor}`}
            aria-label={change.dir}
          />
        )}
      </div>

      {/* Row 3: quality + ST + age */}
      <div className="flex items-center justify-between text-[10px] tabular-nums">
        <span className="flex items-center gap-2">
          <span className={`font-semibold ${qual.fg}`}>{qual.label}</span>
          {tag.current_st != null && (
            <span className="text-muted-foreground">ST {tag.current_st}</span>
          )}
        </span>
        <span className="text-muted-foreground">
          {ageMs != null ? formatRelativeAge(ageMs) : "—"}
        </span>
      </div>

      {/* Row 4: device + protocol + block */}
      <div className="text-[10px] text-muted-foreground truncate">
        {tag.device_name}
        {tag.protocol && <> · {tag.protocol}</>}
        {tag.register_block_name && <> · {tag.register_block_name}</>}
        {tag.address != null && <> @ {tag.address}</>}
      </div>

      {/* Last update absolute timestamp */}
      <div
        className="text-[9px] text-muted-foreground/70 tabular-nums"
        title={tag.last_update_utc ? `UTC ${tag.last_update_utc}` : ""}
      >
        Updated {tag.last_update_utc
          ? formatTimeShort(tag.last_update_utc)
          : "—"}
      </div>
    </div>
  );
}
