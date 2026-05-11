/**
 * Shared device-tabs component. Renders horizontal tabs across the top
 * with "All" first, then one tab per device. Selecting changes the
 * controlled value. Used on Dashboard, Tag Explorer, and Register Blocks.
 */
import { cn } from "@/lib/utils";

type Device = { id: number; name: string };

type Props = {
  devices: Device[];
  value: number | null;  // null = "All"
  onChange: (deviceId: number | null) => void;
  /** Optional counts shown as small badges next to each tab label. */
  counts?: Record<number | "all", number>;
};

export function DeviceTabs({ devices, value, onChange, counts }: Props) {
  return (
    <div className="flex gap-1 border-b overflow-x-auto">
      <TabButton
        active={value === null}
        onClick={() => onChange(null)}
        label="All devices"
        count={counts?.all}
      />
      {devices.map((d) => (
        <TabButton
          key={d.id}
          active={value === d.id}
          onClick={() => onChange(d.id)}
          label={d.name}
          count={counts?.[d.id]}
        />
      ))}
    </div>
  );
}

function TabButton({
  active, onClick, label, count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap",
        active
          ? "border-foreground text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
      {count !== undefined && (
        <span className={cn(
          "ml-2 text-[10px] tabular-nums px-1.5 py-0.5 rounded",
          active ? "bg-foreground/10" : "bg-secondary",
        )}>
          {count}
        </span>
      )}
    </button>
  );
}
