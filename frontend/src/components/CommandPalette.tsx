/**
 * CommandPalette - Cmd/Ctrl+K quick navigation across all pages.
 *
 * Destinations mirror the sidebar (components/layout/Nav.tsx). If you add
 * or rename a nav route there, update COMMANDS below to keep them in sync.
 * Mounted once in AppShell; self-manages open state via a global key
 * listener. Uses semantic tokens so it renders correctly in light + dark.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { Search, CornerDownLeft } from "lucide-react";

type Cmd = { to: string; label: string; section: string };

const COMMANDS: Cmd[] = [
  { to: "/dashboard", label: "Dashboard", section: "Operate" },
  { to: "/trend", label: "Trend", section: "Operate" },
  { to: "/reports", label: "Reports", section: "Operate" },
  { to: "/explorer", label: "Explorer", section: "Operate" },
  { to: "/reports/config", label: "Report Config", section: "Operate" },
  { to: "/alarms", label: "Alarms", section: "Operate" },
  { to: "/tags", label: "Tags", section: "Operate" },
  { to: "/diagnostics", label: "Health", section: "Diagnose" },
  { to: "/audit-log", label: "Audit", section: "Diagnose" },
  { to: "/data-gaps", label: "Gaps", section: "Diagnose" },
  { to: "/historian", label: "Historian", section: "Diagnose" },
  { to: "/config/channels", label: "Networks", section: "Configure" },
  { to: "/config/devices", label: "Devices", section: "Configure" },
  { to: "/config/blocks", label: "Register blocks", section: "Configure" },
  { to: "/global/calc-blocks", label: "Calc tags", section: "Configure" },
  { to: "/config/opc-sources", label: "OPC UA", section: "Configure" },
  { to: "/modbus/registers", label: "Registers", section: "Modbus" },
  { to: "/modbus/frames", label: "Frames", section: "Modbus" },
  { to: "/modbus/write-console", label: "Write", section: "Modbus" },
  { to: "/modbus/write-audit", label: "Write audit", section: "Modbus" },
  { to: "/global/engineering-units", label: "Units", section: "Global/Setup" },
  { to: "/global/alarm-severities", label: "Severities", section: "Global/Setup" },
  { to: "/global/alarm-types", label: "Alarm types", section: "Global/Setup" },
  { to: "/global/groups", label: "Groups", section: "Global/Setup" },
  { to: "/global/named-sets", label: "Enumerations", section: "Global/Setup" },
  { to: "/global/duty-standby-values", label: "Duty/standby", section: "Global/Setup" },
  { to: "/global/settings", label: "General", section: "Global/Setup" },
  { to: "/global/users", label: "Users", section: "Global/Setup" },
  { to: "/help", label: "Help", section: "Help" },
  { to: "/about", label: "About", section: "Help" },
];

export function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Global Cmd/Ctrl+K toggles the palette.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Reset and focus when opened.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSel(0);
    const t = setTimeout(() => inputRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return COMMANDS;
    return COMMANDS.filter(
      (c) =>
        c.label.toLowerCase().includes(q) ||
        c.section.toLowerCase().includes(q) ||
        c.to.toLowerCase().includes(q),
    );
  }, [query]);

  // Keep the selection in range as results shrink.
  useEffect(() => {
    setSel((s) => Math.min(s, Math.max(0, results.length - 1)));
  }, [results.length]);

  if (!open) return null;

  const go = (to: string) => {
    setOpen(false);
    navigate(to);
  };

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center bg-black/50 backdrop-blur-sm pt-[12vh]"
      onClick={() => setOpen(false)}
      role="presentation"
    >
      <div
        className="w-full max-w-lg mx-4 rounded-lg border border-border bg-card shadow-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Command palette"
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSel(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                setOpen(false);
              } else if (e.key === "ArrowDown") {
                e.preventDefault();
                setSel((s) => Math.min(s + 1, results.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setSel((s) => Math.max(s - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                const r = results[sel];
                if (r) go(r.to);
              }
            }}
            placeholder="Jump to a page..."
            className="flex-1 bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground"
          />
          <kbd className="rounded border border-border px-1 py-0.5 text-[10px] text-muted-foreground">
            esc
          </kbd>
        </div>

        <div className="max-h-80 overflow-auto py-1">
          {results.length === 0 ? (
            <div className="px-3 py-6 text-center text-sm text-muted-foreground">
              No matching pages
            </div>
          ) : (
            results.map((c, i) => (
              <button
                key={c.to}
                type="button"
                onClick={() => go(c.to)}
                onMouseMove={() => setSel(i)}
                className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm ${
                  i === sel ? "bg-secondary" : ""
                }`}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-foreground">{c.label}</span>
                  <span className="truncate text-xs text-muted-foreground">{c.to}</span>
                </span>
                <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {c.section}
                </span>
              </button>
            ))
          )}
        </div>

        <div className="flex items-center gap-3 border-t border-border px-3 py-1.5 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <CornerDownLeft className="h-3 w-3" /> open
          </span>
          <span>up/down to navigate</span>
          <span>Cmd/Ctrl+K to toggle</span>
        </div>
      </div>
    </div>
  );
}
