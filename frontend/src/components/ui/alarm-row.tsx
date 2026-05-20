/**
 * Phase 18 — AlarmRow
 *
 * Compact single-row alarm display for use in lists. Communicates four
 * pieces of info at a glance:
 *   - Severity (pill, left)
 *   - Tag name (bold)
 *   - Current value & threshold (monospace, muted)
 *   - Age (right-aligned)
 *
 * Plus optional ack / shelve action buttons that appear on hover.
 *
 * Designed to fit inside a SectionCard with `flush`, where the row's
 * own bottom border creates the list-item separator.
 */
import { cn } from "@/lib/utils";
import { StatusPill, type SeverityVariant } from "./status-pill";

export interface AlarmRowProps {
  severity: SeverityVariant;
  severityLabel?: string;
  tagName: string;
  message?: string;
  ageLabel: string;
  onAck?: () => void;
  onShelve?: () => void;
  onClick?: () => void;
  /** Adds bottom border. Set false on the last row of a list. */
  withSeparator?: boolean;
  className?: string;
}

type SeverityVariantTyped = NonNullable<AlarmRowProps["severity"]>;
// Local alias so TS picks up the imported SeverityVariant correctly
type _ = SeverityVariant;

export function AlarmRow({
  severity, severityLabel, tagName, message, ageLabel,
  onAck, onShelve, onClick, withSeparator = true, className,
}: AlarmRowProps) {
  const interactive = !!onClick;
  return (
    <div
      className={cn(
        "group flex items-center gap-3 py-2",
        withSeparator && "border-b last:border-b-0",
        interactive && "cursor-pointer",
        className,
      )}
      style={{
        borderBottomColor: "var(--separator)",
      }}
      onClick={onClick}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
    >
      <StatusPill
        severity={severity}
        size="sm"
        className="shrink-0 uppercase"
      >
        {severityLabel ?? severity}
      </StatusPill>

      <span className="font-medium text-[12px] truncate min-w-0">
        {tagName}
      </span>

      {message && (
        <span
          className="text-[11px] truncate flex-1 font-mono"
          style={{ color: "var(--ios-gray-1)" }}
        >
          {message}
        </span>
      )}

      {/* Action buttons appear on row hover only */}
      {(onAck || onShelve) && (
        <div className="hidden group-hover:flex items-center gap-1 shrink-0">
          {onAck && (
            <ActionBtn label="Ack" onClick={(e) => { e.stopPropagation(); onAck(); }} />
          )}
          {onShelve && (
            <ActionBtn label="Shelve" onClick={(e) => { e.stopPropagation(); onShelve(); }} />
          )}
        </div>
      )}

      <span
        className="text-[11px] shrink-0 tabular-nums w-12 text-right"
        style={{ color: "var(--ios-gray-1)" }}
      >
        {ageLabel}
      </span>
    </div>
  );
}

function ActionBtn({
  label, onClick,
}: { label: string; onClick: (e: React.MouseEvent) => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-[11px] px-2 py-0.5 rounded-md hover:bg-secondary transition-colors"
      style={{ color: "var(--ios-blue)" }}
    >
      {label}
    </button>
  );
}
