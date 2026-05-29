/**
 * General Settings — Phase 27d MVP.
 *
 * Currently exposes only the plant timezone, which feeds into
 * heatmaps, calendar views, and (future) report scheduler. The page
 * design accommodates future settings (plant name, default units,
 * shift definition, etc.) as additional <Card> blocks.
 *
 * UX decisions:
 *   - Timezone changes are STAGED, not instant. Picking a value in
 *     the dropdown reveals a confirmation banner with [Confirm change]
 *     and [Cancel] buttons. Plant-wide settings affect every operator's
 *     view of timestamps, so a single accidental click shouldn't flip
 *     them — explicit confirmation prevents tail-risk mistakes.
 *   - We don't dump every system_settings row here. Duty/standby has
 *     its own page; future settings get their own cards. This page
 *     is operator-facing, not a sysadmin key/value browser.
 *
 * Implementation notes:
 *   - No shadcn Command/Popover/sonner — picker is hand-rolled with
 *     useState + click-outside handler. Status feedback is an inline
 *     badge next to the field label.
 *   - Uses CSS variables (--bg-elevated, --border-default, etc.) so
 *     it adopts the iOS-segmented theme used elsewhere.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronDown, Loader2, Search, X } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { HelpTip } from "@/components/ui/help-tip";
import { help } from "@/lib/help-text";
import { Button } from "@/components/ui/button";  // Phase 27e
import { Gate } from "@/lib/rbac";
import { Input } from "@/components/ui/input";  // Phase 27e
// Phase 27d.1 — time format moved here from the top header. Uses the
// existing context (localStorage-backed, per-browser preference) so
// behavior is unchanged; only the UI surface migrated.
import { useTimeFormat, type TimeFormatMode } from "@/lib/timeFormat";

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------

interface TimezoneOption {
  value: string;
  label: string;
  offset_minutes: number;
}

interface TimezoneListResponse {
  timezones: TimezoneOption[];
  current: string;
}

interface SettingsResponse {
  settings: Record<string, string>;
}

async function fetchSettings(): Promise<SettingsResponse> {
  const res = await fetch("/api/settings");
  if (!res.ok) throw new Error(`GET /api/settings failed: ${res.status}`);
  return res.json();
}

async function fetchTimezones(): Promise<TimezoneListResponse> {
  const res = await fetch("/api/settings/timezones");
  if (!res.ok) throw new Error(`GET /api/settings/timezones failed: ${res.status}`);
  return res.json();
}

async function patchSettings(updates: Record<string, string>): Promise<SettingsResponse> {
  const res = await fetch("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ updates }),
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = text || `PATCH /api/settings failed: ${res.status}`;
    try {
      const parsed = JSON.parse(text);
      if (parsed.detail) msg = parsed.detail;
    } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Searchable Timezone Picker (self-contained)
// ---------------------------------------------------------------------------

interface TimezonePickerProps {
  value: string;
  options: TimezoneOption[];
  disabled?: boolean;
  onChange: (newValue: string) => void;
}

function TimezonePicker({ value, options, disabled, onChange }: TimezonePickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch("");
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  useEffect(() => {
    if (open && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [open]);

  const filteredOptions = useMemo(() => {
    if (!search) return options;
    const needle = search.toLowerCase();
    return options.filter((opt) => opt.label.toLowerCase().includes(needle));
  }, [options, search]);

  const currentLabel = useMemo(() => {
    const found = options.find((opt) => opt.value === value);
    return found ? found.label : value;
  }, [value, options]);

  const handleSelect = (newValue: string) => {
    setOpen(false);
    setSearch("");
    onChange(newValue);
  };

  return (
    <div ref={containerRef} className="relative w-full">
      <button
        type="button"
        onClick={() => !disabled && setOpen((p) => !p)}
        disabled={disabled}
        className="flex w-full items-center justify-between rounded-md border px-3 py-2 text-sm
                   transition-colors disabled:cursor-not-allowed disabled:opacity-60"
        style={{
          backgroundColor: "var(--bg-elevated)",
          borderColor: "var(--border-default)",
          color: "var(--text-primary)",
        }}
      >
        <span className="truncate">{currentLabel}</span>
        <ChevronDown
          className="ml-2 h-4 w-4 shrink-0 transition-transform"
          style={{
            opacity: 0.5,
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
          }}
        />
      </button>

      {open && (
        <div
          className="absolute z-50 mt-1 w-full overflow-hidden rounded-md border shadow-lg"
          style={{
            backgroundColor: "var(--bg-elevated)",
            borderColor: "var(--border-default)",
          }}
        >
          <div
            className="flex items-center border-b px-3 py-2"
            style={{ borderColor: "var(--border-default)" }}
          >
            <Search className="mr-2 h-4 w-4" style={{ opacity: 0.5 }} />
            <input
              ref={searchInputRef}
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search timezone (e.g. Kolkata, London, UTC)..."
              className="flex-1 bg-transparent text-sm outline-none placeholder:opacity-50"
              style={{ color: "var(--text-primary)" }}
            />
          </div>

          <div className="max-h-72 overflow-y-auto">
            {filteredOptions.length === 0 ? (
              <div
                className="px-3 py-4 text-center text-sm"
                style={{ color: "var(--text-secondary)" }}
              >
                No timezone matches that search.
              </div>
            ) : (
              filteredOptions.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => handleSelect(opt.value)}
                  className="flex w-full items-center px-3 py-2 text-left text-sm transition-colors hover:opacity-90"
                  style={{
                    backgroundColor:
                      opt.value === value ? "var(--ios-gray-5)" : "transparent",
                    color: "var(--text-primary)",
                  }}
                >
                  <Check
                    className="mr-2 h-4 w-4 shrink-0"
                    style={{ opacity: opt.value === value ? 1 : 0 }}
                  />
                  <span className="truncate">{opt.label}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline status indicator
// ---------------------------------------------------------------------------

type InlineStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "error"; message: string };

function StatusBadge({ status }: { status: InlineStatus }) {
  if (status.kind === "idle") return null;
  if (status.kind === "saving") {
    return (
      <span
        className="flex items-center gap-1 text-xs"
        style={{ color: "var(--text-secondary)" }}
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        Saving…
      </span>
    );
  }
  if (status.kind === "saved") {
    return (
      <span className="flex items-center gap-1 text-xs" style={{ color: "var(--success)" }}>
        <Check className="h-3 w-3" />
        Saved
      </span>
    );
  }
  return (
    <span className="text-xs" style={{ color: "var(--danger)" }}>
      {status.message}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Confirmation banner — shown when there's a pending timezone change
// ---------------------------------------------------------------------------

interface ConfirmBannerProps {
  fromLabel: string;
  toLabel: string;
  disabled: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

function ConfirmBanner({ fromLabel, toLabel, disabled, onConfirm, onCancel }: ConfirmBannerProps) {
  return (
    <div
      className="flex flex-col gap-3 rounded-md border p-3"
      style={{
        backgroundColor: "var(--ios-yellow-soft, rgba(255, 204, 0, 0.08))",
        borderColor: "var(--ios-yellow, #ffcc00)",
      }}
    >
      <div className="flex items-start gap-2">
        <AlertTriangle
          className="h-4 w-4 shrink-0 mt-0.5"
          style={{ color: "var(--ios-yellow, #d97706)" }}
        />
        <div className="text-sm" style={{ color: "var(--text-primary)" }}>
          <strong>Confirm timezone change.</strong>{" "}
          You're about to change the plant timezone from{" "}
          <code className="text-xs">{fromLabel}</code> to{" "}
          <code className="text-xs">{toLabel}</code>. This affects how
          timestamps are displayed in heatmaps, calendar views, alarm windows,
          and (future) scheduled reports. All operators will see the new
          timezone within ~60 seconds.
        </div>
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onConfirm}
          disabled={disabled}
          className="rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          style={{
            backgroundColor: "var(--ios-blue)",
            color: "#fff",
          }}
        >
          Confirm change
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={disabled}
          className="flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          style={{
            backgroundColor: "transparent",
            borderColor: "var(--border-default)",
            color: "var(--text-primary)",
          }}
        >
          <X className="h-3.5 w-3.5" />
          Cancel
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Settings Page
// ---------------------------------------------------------------------------

export default function Settings() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<InlineStatus>({ kind: "idle" });
  // Pending change — set when operator picks a new value, cleared on
  // Confirm (after successful save) or Cancel.
  const [pendingTimezone, setPendingTimezone] = useState<string | null>(null);

  // Auto-hide "Saved" after 2 seconds
  useEffect(() => {
    if (status.kind === "saved") {
      const t = setTimeout(() => setStatus({ kind: "idle" }), 2000);
      return () => clearTimeout(t);
    }
  }, [status]);

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });

  const timezonesQuery = useQuery({
    queryKey: ["settings", "timezones"],
    queryFn: fetchTimezones,
    staleTime: 5 * 60 * 1000,
  });

  const mutation = useMutation({
    mutationFn: patchSettings,
    onMutate: () => setStatus({ kind: "saving" }),
    onSuccess: () => {
      // Invalidate broadly — timezone changes affect heatmaps, calendar
      // heatmap, trend chart timestamps, and (future) report schedules.
      queryClient.invalidateQueries();
      setStatus({ kind: "saved" });
      setPendingTimezone(null);
    },
    onError: (err: Error) => {
      setStatus({ kind: "error", message: err.message });
    },
  });

  const currentTimezone =
    settingsQuery.data?.settings["app.timezone"] ??
    timezonesQuery.data?.current ??
    "Asia/Kolkata";

  // What the picker visually displays — pending value if set, else live value
  const displayedTimezone = pendingTimezone ?? currentTimezone;

  const isLoading = settingsQuery.isLoading || timezonesQuery.isLoading;
  const isSaving = mutation.isPending;
  const hasPendingChange = pendingTimezone !== null && pendingTimezone !== currentTimezone;

  // Helper to resolve a timezone value to its display label
  const labelFor = (tzValue: string): string => {
    const found = timezonesQuery.data?.timezones.find((o) => o.value === tzValue);
    return found ? found.label : tzValue;
  };

  const handlePickerChange = (newValue: string) => {
    if (newValue === currentTimezone) {
      // Reverting to the live value — clear any pending state
      setPendingTimezone(null);
      setStatus({ kind: "idle" });
      return;
    }
    setPendingTimezone(newValue);
    setStatus({ kind: "idle" });
  };

  const handleConfirm = () => {
    if (!pendingTimezone) return;
    mutation.mutate({ "app.timezone": pendingTimezone });
  };

  const handleCancel = () => {
    setPendingTimezone(null);
    setStatus({ kind: "idle" });
  };

  return (
    <div className="space-y-6">
      {/* Locale section */}
      <Card>
        <CardHeader>
          <CardTitle className="inline-flex items-center">Locale<HelpTip entry={help.settings.timezone} /></CardTitle>
          <CardDescription>
            How dates and times are displayed across dashboards, heatmaps, and reports.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium" htmlFor="timezone-picker">
                Timezone
              </label>
              <StatusBadge status={status} />
            </div>
            {isLoading ? (
              <div
                className="flex h-10 items-center text-sm"
                style={{ color: "var(--text-secondary)" }}
              >
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading timezones…
              </div>
            ) : (
              <TimezonePicker
                value={displayedTimezone}
                options={timezonesQuery.data?.timezones ?? []}
                disabled={isSaving}
                onChange={handlePickerChange}
              />
            )}

            {/* Confirmation banner — appears only when there's a pending change */}
            {hasPendingChange && pendingTimezone && (
              <ConfirmBanner
                fromLabel={labelFor(currentTimezone)}
                toLabel={labelFor(pendingTimezone)}
                disabled={isSaving}
                onConfirm={handleConfirm}
                onCancel={handleCancel}
              />
            )}

            <p
              className="text-xs"
              style={{ color: "var(--text-secondary)" }}
            >
              IANA timezone identifier. Affects calendar heatmaps, quality heatmaps,
              alarm density windows, and time-window calculations for scheduled reports.
              The plant's data is always stored in UTC; only the display changes.
            </p>
          </div>

          {/* ─────────────────────────────────────────────────────────
              Time format — Phase 27d.1.
              Per-browser preference (localStorage). Applies instantly,
              no confirmation banner — only affects the current user's
              view, not the plant-wide setting.
              ───────────────────────────────────────────────────────── */}
          <TimeFormatSection />
        </CardContent>
      </Card>

      {/* Phase 27e - ShiftsCard */}
      <ShiftsCard />
    </div>
  );
}


/**
 * Time format picker — segmented control with three options.
 * Reads/writes via the existing TimeFormatProvider, so the choice
 * propagates immediately to every consumer (trend chart axes, summary
 * panel tooltips, alarm timestamps, raw data table, etc.).
 */
function TimeFormatSection() {
  const { mode, setMode, is24h } = useTimeFormat();

  const options: { value: TimeFormatMode; label: string; sublabel: string }[] = [
    { value: "24h",  label: "24-hour", sublabel: "HH:MM:SS" },
    { value: "12h",  label: "12-hour", sublabel: "h:MM:SS AM/PM" },
    { value: "auto", label: "Auto",    sublabel: `Browser language (${is24h ? "24h" : "12h"})` },
  ];

  return (
    <div
      className="space-y-2 pt-4 border-t"
      style={{ borderColor: "var(--separator)" }}
    >
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium">
          Time format
        </label>
      </div>

      {/* Segmented control — three pills. Matches the 2w/4w/8w/12w
          pickers used elsewhere (calendar heatmap, etc.) for visual
          consistency across the app. */}
      <div
        className="inline-flex p-0.5 rounded-md"
        style={{ backgroundColor: "var(--ios-gray-5)" }}
        role="radiogroup"
        aria-label="Time format"
      >
        {options.map((o) => {
          const active = mode === o.value;
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setMode(o.value)}
              className="text-xs font-medium rounded px-3 py-1.5 transition-colors"
              style={
                active
                  ? {
                      backgroundColor: "var(--bg-elevated)",
                      color: "var(--text-primary)",
                      boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
                    }
                  : { color: "var(--text-secondary)" }
              }
              title={o.sublabel}
            >
              {o.label}
            </button>
          );
        })}
      </div>

      <p
        className="text-xs"
        style={{ color: "var(--text-secondary)" }}
      >
        Industrial convention is 24-hour. <b>Auto</b> follows your browser's
        language preference (which the browser, not the OS, resolves —
        currently <span className="font-medium">{is24h ? "24-hour" : "12-hour"}</span>).
        This is a per-browser preference; other operators see whatever they
        picked themselves. Plant-wide data storage is unaffected.
      </p>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Phase 27e - ShiftsCard: edit the plant shift schedule (A/B/C + times).
// ---------------------------------------------------------------------------
type ShiftRow = { code: string; label: string; start: string };
type ShiftsCfg = { enabled: boolean; shifts: ShiftRow[] };

function ShiftsCard() {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<ShiftsCfg | null>(null);
  const [msg, setMsg] = useState<{ kind: "idle" | "saved" | "error"; text?: string }>({ kind: "idle" });

  const cfg = useQuery({
    queryKey: ["settings-shifts"],
    queryFn: async () => {
      const res = await fetch("/api/settings/shifts", {
        headers: localStorage.getItem("induvista:token")
          ? { Authorization: `Bearer ${localStorage.getItem("induvista:token")}` }
          : undefined,
      });
      if (!res.ok) throw new Error(`GET /api/settings/shifts ${res.status}`);
      return (await res.json()) as ShiftsCfg;
    },
  });

  // Initialize the editable draft once the config loads.
  const view = draft ?? cfg.data ?? null;

  const save = useMutation({
    mutationFn: async (body: ShiftsCfg) => {
      const res = await fetch("/api/settings/shifts", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...(localStorage.getItem("induvista:token")
            ? { Authorization: `Bearer ${localStorage.getItem("induvista:token")}` }
            : {}),
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `PATCH failed: ${res.status}`);
      }
      return (await res.json()) as ShiftsCfg;
    },
    onSuccess: (data) => {
      setMsg({ kind: "saved" });
      setDraft(null);
      qc.setQueryData(["settings-shifts"], data);
      setTimeout(() => setMsg({ kind: "idle" }), 2000);
    },
    onError: (e: Error) => setMsg({ kind: "error", text: e.message }),
  });

  function update(i: number, field: keyof ShiftRow, value: string) {
    if (!view) return;
    const next = { ...view, shifts: view.shifts.map((s, idx) => idx === i ? { ...s, [field]: value } : s) };
    setDraft(next);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="inline-flex items-center">Shifts<HelpTip entry={help.settings.shifts} /></CardTitle>
        <CardDescription>
          Define the plant shift schedule. The sidebar clock shows the current
          shift based on these start times (plant local time). Shifts are
          contiguous — each runs until the next one begins; the last wraps past
          midnight.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {cfg.isLoading && <p className="text-sm" style={{ color: "var(--text-secondary)" }}>Loading…</p>}
        {view && (
          <>
            <div className="grid grid-cols-[60px_1fr_110px] gap-2 text-[11px] font-medium uppercase tracking-wide"
                 style={{ color: "var(--text-secondary)" }}>
              <span>Code</span><span>Label</span><span>Start (HH:MM)</span>
            </div>
            {view.shifts.map((s, i) => (
              <div key={i} className="grid grid-cols-[60px_1fr_110px] gap-2 items-center">
                <Input value={s.code} maxLength={4} onChange={(e) => update(i, "code", e.target.value)} />
                <Input value={s.label} onChange={(e) => update(i, "label", e.target.value)} />
                <Input value={s.start} placeholder="HH:MM" onChange={(e) => update(i, "start", e.target.value)} />
              </div>
            ))}
            <div className="flex items-center gap-3 pt-1">
              <Gate cap="administer" mode="disable">
              <Button
                onClick={() => view && save.mutate(view)}
                disabled={save.isPending || !draft}
              >
                {save.isPending ? "Saving…" : "Save shifts"}
              </Button>
              </Gate>
              {draft && (
                <Button variant="ghost" onClick={() => setDraft(null)}>Reset</Button>
              )}
              {msg.kind === "saved" && (
                <span className="text-sm" style={{ color: "var(--ios-green, #34c759)" }}>Saved.</span>
              )}
              {msg.kind === "error" && (
                <span className="text-sm" style={{ color: "var(--ios-red)" }}>{msg.text}</span>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}