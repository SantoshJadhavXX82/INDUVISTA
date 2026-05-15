/**
 * Phase 11 — Block Coverage Map.
 *
 * Renders a horizontal SVG bar showing tag positions within a register block.
 * One column per logical address; filled with the tag's data-type color if
 * a tag exists there, light gray for gaps.
 *
 * Effective span semantics:
 *   • STANDARD addressing — each tag occupies `register_count` contiguous
 *     16-bit registers, so a float32 tag at 7001 covers [7001, 7002].
 *   • Enron addressing — each tag occupies exactly 1 logical address
 *     regardless of register_count. A float32 at 7001 covers only 7001.
 *
 * The bar makes addressing-pattern errors obvious. The mole-% gap=2 case
 * we hit in Phase 10.2 would have shown 16 alternating dark/light columns
 * across a count=32 block — visually screaming "you're skipping half."
 */
import { cn } from "@/lib/utils";

const TYPE_COLORS: Record<string, string> = {
  bool: "#a78bfa",      // violet
  int16: "#60a5fa",     // blue
  uint16: "#3b82f6",
  int32: "#2563eb",
  uint32: "#1d4ed8",
  float32: "#10b981",   // emerald
  int64: "#0891b2",
  uint64: "#0e7490",
  float64: "#065f46",
  string: "#f59e0b",    // amber
};

interface CoverageTag {
  id: number;
  name: string;
  address: number;
  register_count: number;
  data_type: string;
}

interface BlockCoverageMapProps {
  start_address: number;
  count: number;                       // block size: logical addresses (Enron) or registers (Standard)
  addressing_mode: string;             // 'STANDARD' | 'ENRON_HOLDING' | 'ENRON_INPUT'
  tags: CoverageTag[];
  height?: number;                     // SVG height in px (default 24)
  className?: string;
}

export function BlockCoverageMap({
  start_address, count, addressing_mode, tags,
  height = 24, className,
}: BlockCoverageMapProps) {
  const isEnron = addressing_mode === "ENRON_HOLDING" || addressing_mode === "ENRON_INPUT";

  // Build a sparse address → tag map. For STANDARD, fill register_count slots
  // per tag. For Enron, fill exactly 1 slot per tag (effective_span = 1).
  const slotMap = new Map<number, CoverageTag>();
  for (const t of tags) {
    const span = isEnron ? 1 : Math.max(1, t.register_count);
    for (let i = 0; i < span; i++) {
      slotMap.set(t.address + i, t);
    }
  }

  const slots = Array.from({ length: count }, (_, i) => start_address + i);
  const filled = slots.filter(s => slotMap.has(s)).length;
  const coverage = count > 0 ? (filled / count) * 100 : 0;

  // Compute width per cell; cap the visual cell width so a 1024-address
  // block doesn't show invisible 1-pixel slivers
  const SVG_WIDTH = 800;
  const cellWidth = Math.max(1, SVG_WIDTH / count);
  const showLabels = cellWidth >= 14;

  // Detect the gap=2 antipattern: > 4 tags AND every-other-slot
  // alternates filled/empty. Highlight in the summary.
  let antipattern = false;
  if (isEnron && tags.length >= 4) {
    const sorted = [...tags].sort((a, b) => a.address - b.address);
    const gaps = sorted.slice(1).map((t, i) => t.address - sorted[i].address);
    if (gaps.length >= 3 && gaps.every(g => g === 2)) {
      antipattern = true;
    }
  }

  return (
    <div className={cn("space-y-1", className)}>
      <svg
        viewBox={`0 0 ${SVG_WIDTH} ${height}`}
        preserveAspectRatio="none"
        className="w-full border border-gray-200 rounded"
        style={{ height }}
      >
        {slots.map((addr, i) => {
          const tag = slotMap.get(addr);
          const x = i * cellWidth;
          const color = tag
            ? TYPE_COLORS[tag.data_type] ?? "#94a3b8"
            : "#f3f4f6";
          return (
            <g key={addr}>
              <rect
                x={x}
                y={0}
                width={cellWidth - 0.5}
                height={height}
                fill={color}
              >
                <title>
                  {addr}: {tag ? `${tag.name} (${tag.data_type})` : "empty"}
                </title>
              </rect>
              {showLabels && tag && (
                <text
                  x={x + cellWidth / 2}
                  y={height / 2 + 3}
                  textAnchor="middle"
                  fontSize="9"
                  fill="white"
                  fontFamily="monospace"
                  style={{ pointerEvents: "none" }}
                >
                  {addr}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="flex items-center justify-between text-[10px] text-muted-foreground">
        <span>
          {start_address} → {start_address + count - 1} · {filled}/{count} addresses populated
          {" "}({coverage.toFixed(0)}%)
        </span>
        {antipattern && (
          <span className="font-medium text-amber-700" title="See Phase 9.1.2: Enron tags should use gap=1 addressing">
            ⚠ gap=2 pattern detected — likely wastes half the read
          </span>
        )}
      </div>
    </div>
  );
}
