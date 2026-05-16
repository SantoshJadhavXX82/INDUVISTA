/**
 * Phase 13.7 - Rich hover tooltip for the trend chart.
 *
 * Implements spec section 8.3 - shows 13 fields per series at cursor:
 *   timestamp local, timestamp UTC, tag name, description, value,
 *   engineering unit, ST integer, quality class, device, protocol,
 *   block, group (channel), address.
 *
 * Event/annotation details are spec'd too but those modules don't exist
 * yet - they'll surface here automatically when they do.
 *
 * Positioning:
 *   The tooltip is absolutely positioned inside the chart container
 *   (which TrendChart's wrapper makes position:relative). We offset the
 *   tooltip from the cursor, flipping to the opposite side when the
 *   tooltip would clip the right or bottom edge of the container.
 */
import { useTimeFormat } from "@/lib/timeFormat";
import type { TrendTag } from "@/types/api";

/** What the chart's cursor hook hands us. */
export type CursorState = {
  timestampSec: number;       // unix seconds
  mouseLeft: number;          // px from chart left edge
  mouseTop: number;           // px from chart top edge
  series: CursorSeriesData[]; // one per chart series
};

export type CursorSeriesData = {
  tagId: number;
  tagName: string;
  color: string;
  engineeringUnit: string | null;
  value: number | null;
  st: number | null;          // raw ST integer when in raw mode
  goodCount: number | null;   // bucket good count (aggregated mode)
  badCount: number | null;    // bucket bad count (aggregated mode)
  uncertainCount?: number | null;
};

type TrendTooltipProps = {
  cursor: CursorState | null;
  tagsMeta: Record<number, TrendTag>;
  isAggregated: boolean;      // affects how quality is shown
  /** "full" (all spec 8.3 fields), "compact" (just value + quality),
   *  or "off" (don't render at all). */
  mode?: "full" | "compact" | "off";
  /** True when the operator has clicked the chart to pin the tooltip in
   *  place. Pinned tooltips become fully interactive (scrollable) and
   *  display a "pinned" banner with a manual unpin button. */
  isPinned?: boolean;
  onUnpin?: () => void;
  containerWidth: number;
  containerHeight: number;
};

const OFFSET = 14;
const TOOLTIP_WIDTH_FULL = 360;
const TOOLTIP_WIDTH_COMPACT = 240;
const TOOLTIP_MAX_HEIGHT = 380;

/** ST integer to industrial-quality class string (per spec section 9.2). */
function classifyST(st: number | null | undefined): "good" | "uncertain" | "bad" | "unknown" {
  if (st == null) return "unknown";
  if (st >= 128) return "good";        // covers 192-255 and 128-191
  if (st >= 64)  return "uncertain";   // 64-127
  return "bad";                          // 0-63
}

/**
 * Classify a bucket's quality from good/bad counts. Used in aggregated
 * mode where individual ST values aren't preserved per bucket.
 *
 * Rule:  any bad   -> BAD
 *        any uncertain (good < total) -> UNCERTAIN
 *        else      -> GOOD
 *
 * If counts aren't available, returns "unknown" so the chip falls back
 * to "—" rather than misleading the operator.
 */
function classifyBucket(
  good: number | null | undefined,
  bad: number | null | undefined,
  uncertain?: number | null | undefined,
): "good" | "uncertain" | "bad" | "unknown" {
  if (good == null && bad == null && uncertain == null) return "unknown";
  if ((bad ?? 0) > 0) return "bad";
  if ((uncertain ?? 0) > 0) return "uncertain";
  if ((good ?? 0) > 0) return "good";
  return "unknown";
}

function qualityChip(kind: "good" | "uncertain" | "bad" | "unknown") {
  const map = {
    good:      { bg: "bg-emerald-100", fg: "text-emerald-800", text: "GOOD" },
    uncertain: { bg: "bg-amber-100",   fg: "text-amber-800",   text: "UNCERTAIN" },
    bad:       { bg: "bg-red-100",     fg: "text-red-800",     text: "BAD" },
    unknown:   { bg: "bg-secondary",   fg: "text-muted-foreground", text: "—" },
  }[kind];
  return (
    <span className={`inline-flex items-center rounded-sm px-1.5 py-0 text-[9px] font-semibold uppercase tracking-wider ${map.bg} ${map.fg}`}>
      {map.text}
    </span>
  );
}

function formatNum(v: number | null): string {
  if (v == null) return "—";
  // Tight industrial formatting - 4 decimals max, strip trailing zeros
  const abs = Math.abs(v);
  let str: string;
  if (abs >= 100)      str = v.toFixed(2);
  else if (abs >= 1)   str = v.toFixed(3);
  else                  str = v.toFixed(4);
  return str.replace(/\.?0+$/, "");
}

export default function TrendTooltip({
  cursor, tagsMeta, isAggregated, mode = "full",
  isPinned = false, onUnpin,
  containerWidth, containerHeight,
}: TrendTooltipProps) {
  const { formatDateTime } = useTimeFormat();
  if (!cursor || mode === "off") return null;

  const isCompact = mode === "compact";
  const TOOLTIP_WIDTH = isCompact ? TOOLTIP_WIDTH_COMPACT : TOOLTIP_WIDTH_FULL;

  // Edge avoidance - flip to the other side if we'd overflow the container.
  const wouldOverflowRight = cursor.mouseLeft + OFFSET + TOOLTIP_WIDTH > containerWidth;
  const left = wouldOverflowRight
    ? Math.max(4, cursor.mouseLeft - OFFSET - TOOLTIP_WIDTH)
    : cursor.mouseLeft + OFFSET;

  const wouldOverflowBottom = cursor.mouseTop + OFFSET + TOOLTIP_MAX_HEIGHT > containerHeight;
  const top = wouldOverflowBottom
    ? Math.max(4, containerHeight - TOOLTIP_MAX_HEIGHT - 4)
    : cursor.mouseTop + OFFSET;

  const utcIso = new Date(cursor.timestampSec * 1000).toISOString();

  return (
    <div
      className={`absolute z-50 ${isPinned ? "pointer-events-auto" : "pointer-events-none"}`}
      style={{
        left, top,
        width: TOOLTIP_WIDTH,
        maxHeight: TOOLTIP_MAX_HEIGHT,
      }}
    >
      <div className={`bg-card border ${isPinned ? "border-blue-400 ring-1 ring-blue-200" : "border-border"} rounded-md shadow-lg overflow-hidden text-xs`}>
        {/* Pin indicator banner */}
        {isPinned && (
          <div className="px-3 py-1 bg-blue-50 text-blue-800 text-[10px] flex items-center justify-between border-b border-blue-200">
            <span>📌 Pinned — click chart to unpin</span>
            <button
              type="button"
              onClick={onUnpin}
              className="hover:bg-blue-100 rounded px-1.5 -my-0.5 text-blue-700"
              aria-label="Unpin tooltip"
            >
              ×
            </button>
          </div>
        )}

        {/* Header: timestamps */}
        <div className="px-3 py-2 border-b border-border bg-secondary/30">
          <div className="font-semibold tabular-nums">
            {formatDateTime(cursor.timestampSec * 1000)}
          </div>
          {!isCompact && (
            <div className="text-[10px] text-muted-foreground tabular-nums">
              UTC {utcIso}
            </div>
          )}
        </div>

        {/* Per-series rows. Inner div is scrollable; pointer-events come
            from the outer wrapper above (auto only when pinned). */}
        <div className="overflow-y-auto" style={{ maxHeight: TOOLTIP_MAX_HEIGHT - 60 - (isPinned ? 28 : 0) }}>
          {cursor.series.map((s) => {
            const tag = tagsMeta[s.tagId];
            // In aggregated mode the ST integer per point isn't meaningful
            // (it'd just be the bucket's first row), so classify from the
            // bucket's good/bad counts instead. In raw mode the per-point
            // ST is authoritative.
            const stClass = isAggregated
              ? classifyBucket(s.goodCount, s.badCount, s.uncertainCount)
              : classifyST(s.st);
            return (
              <div key={s.tagId} className="px-3 py-2 border-b border-border last:border-b-0">
                {/* Name + color swatch */}
                <div className="flex items-center gap-2 mb-1">
                  <span
                    aria-hidden
                    className="inline-block w-3 h-3 rounded-sm flex-shrink-0"
                    style={{ backgroundColor: s.color }}
                  />
                  <span className="font-semibold truncate">{s.tagName}</span>
                  {!isCompact && tag?.description && (
                    <span className="text-[10px] text-muted-foreground truncate">
                      {tag.description}
                    </span>
                  )}
                </div>

                {/* Value + quality */}
                <div className="flex items-center justify-between gap-2 mb-1">
                  <div className="flex items-baseline gap-1 tabular-nums">
                    <span className="text-base font-semibold">
                      {formatNum(s.value)}
                    </span>
                    {s.engineeringUnit && (
                      <span className="text-[10px] text-muted-foreground">
                        {s.engineeringUnit}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    {!isCompact && !isAggregated && s.st != null && (
                      <span className="text-[10px] text-muted-foreground tabular-nums">
                        ST {s.st}
                      </span>
                    )}
                    {qualityChip(stClass)}
                  </div>
                </div>

                {/* Aggregated mode: show bucket counts (full only) */}
                {!isCompact && isAggregated && (s.goodCount != null || s.badCount != null) && (
                  <div className="text-[10px] text-muted-foreground tabular-nums mb-1 flex gap-2">
                    {s.goodCount != null && <span>Good {s.goodCount}</span>}
                    {s.uncertainCount != null && s.uncertainCount > 0 && (
                      <span className="text-amber-700">Uncertain {s.uncertainCount}</span>
                    )}
                    {s.badCount != null && s.badCount > 0 && (
                      <span className="text-red-700">Bad {s.badCount}</span>
                    )}
                  </div>
                )}

                {/* Metadata grid - device / protocol / block / address / channel (full only) */}
                {!isCompact && tag && (
                  <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground mt-1">
                    {tag.device_name && (
                      <Meta label="Device" value={tag.device_name} />
                    )}
                    {tag.protocol && (
                      <Meta label="Protocol" value={tag.protocol} />
                    )}
                    {tag.register_block_name && (
                      <Meta label="Block" value={tag.register_block_name} />
                    )}
                    {tag.address != null && (
                      <Meta label="Address" value={String(tag.address)} />
                    )}
                    {tag.channel_name && (
                      <Meta label="Channel" value={tag.channel_name} />
                    )}
                    {tag.data_type && (
                      <Meta label="Type" value={tag.data_type} />
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-1 truncate">
      <span className="font-medium text-foreground/60">{label}:</span>
      <span className="truncate">{value}</span>
    </div>
  );
}
