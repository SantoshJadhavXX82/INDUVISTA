import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Activity, Bell, ChevronDown, KeyRound, LogOut } from "lucide-react";
import { useNavigate } from "react-router";
import { type HealthResponse } from "@/types/api";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import Nav from "@/components/layout/Nav";
import MobileTabBar from "@/components/layout/MobileTabBar";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { useIsMobile } from "@/lib/use-media-query";
import { useAuth } from "@/lib/auth";
import { useTimeFormat } from "@/lib/timeFormat";
import { currentShift, isoWeek, formatUptimeShort, type ShiftsConfig } from "@/lib/shifts";

export default function AppShell({ children }: { children: React.ReactNode }) {
  // Light heartbeat at the app shell — keeps the node/version visible and
  // tells the user whether the API is reachable at all. /health is not under
  // /api, so we bypass the api wrapper here.
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const res = await fetch("/health");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as HealthResponse;
    },
    refetchInterval: 10_000,
    retry: false,
  });

  // Phase 19 — responsive: sidebar on desktop, bottom tab bar on mobile.
  const isMobile = useIsMobile();

  return (
    <div className="flex h-screen" style={{ backgroundColor: "var(--bg-grouped)", color: "var(--ios-gray-1)" }}>
      {!isMobile && (
      <aside
        className="w-60 shrink-0 flex flex-col h-screen min-h-0"  /* scrollfix */
        style={{
          backgroundColor: "var(--bg-elevated)",
          borderRight: "0.5px solid var(--separator)",
        }}
      >
        <div className="px-4 py-4" style={{ borderBottom: "0.5px solid var(--separator)" }}>
          <div className="flex items-center gap-2.5">
            <div
              className="shrink-0 flex items-center justify-center"
              style={{
                width: 38,
                height: 38,
                backgroundColor: "var(--ios-blue)",
                borderRadius: 9,
                boxShadow: "0 1px 2px rgba(0, 0, 0, 0.08)",
              }}
              aria-label="InduVista"
              role="img"
            >
              <Activity className="text-white" style={{ width: 22, height: 22 }} strokeWidth={2.5} />
            </div>
            <div>
              <div
                className="text-lg font-semibold tracking-tight leading-tight"
                style={{ color: "var(--text-primary)", letterSpacing: "-0.02em" }}
              >InduVista</div>
              <div
                className="text-[10px] uppercase tracking-wider mt-0.5"
                style={{ color: "var(--text-secondary)" }}
              >
                Industrial Reporting Tool
              </div>
            </div>
          </div>
        </div>
        <Nav />
        {/* Live clock pinned to the bottom of the sidebar (mt-auto pushes it
            down). Control-room convention: a persistent wall-clock always in
            the same spot, out of the way of the main content. */}
        <SidebarClock />
      </aside>
      )}

      <div className="flex-1 flex flex-col overflow-hidden">
        <header
          className="h-12 flex items-center justify-end gap-3 px-4"
          style={{
            backgroundColor: "var(--bg-elevated)",
            borderBottom: "0.5px solid var(--separator)",
          }}
        >
          {/* Diagnostics cluster — de-emphasized (muted, smaller). Status only. */}
          <div className="hidden sm:flex items-center gap-2" style={{ color: "var(--text-secondary)" }}>
            {health.isError ? (
              <Badge variant="destructive" className="text-xs">backend offline</Badge>
            ) : health.data ? (
              <>
                <Badge variant="outline" className="font-mono text-[11px] opacity-70">
                  {health.data.app_env}
                </Badge>
                {/* HA node role — relabeled 'node:' so it doesn't collide
                    with the USER role shown in the account menu. */}
                <Badge
                  variant={health.data.role === "active" ? "success" : "warning"}
                  className="font-mono text-[11px]"
                  title="High-availability node role (active / passive)"
                >
                  node: {health.data.role}
                </Badge>
                <span
                  className="text-[11px] tabular-nums opacity-70"
                  title={
                    `Backend uptime: ${formatUptime(health.data.uptime_sec)}\n` +
                    `Started: ${health.data.started_at}\n` +
                    `Cycle count: ${health.data.cycle_count}\n` +
                    `Migration: ${health.data.migration_version ?? "—"}`
                  }
                >
                  db {health.data.db_latency_ms.toFixed(1)} ms
                </span>
              </>
            ) : (
              <span className="text-xs">connecting…</span>
            )}
          </div>

          {/* Divider */}
          <span
            className="hidden sm:block"
            style={{ width: 1, height: 20, backgroundColor: "var(--separator)" }}
          />

          {/* Notification bell — live active-alarm count; click → Alarms page. */}
          <NotificationBell />

          <ThemeToggle />

          {/* Account menu — primary, rightmost. Identity + actions. */}
          <UserMenu />
        </header>

        <main
          className="flex-1 overflow-auto"
          style={{
            color: "var(--text-primary)",
            padding: isMobile ? "12px 12px 80px" : "24px",
          }}
        >{children}</main>
      </div>
      {isMobile && <MobileTabBar />}
    </div>
  );
}

/**
 * Live wall-clock + shift + uptime, pinned to the bottom of the sidebar.
 *   20:03:12                         (large, with seconds, 24h/12h pref)
 *   Thursday, 28-May-2026 (WK 22)    (day, date, ISO week)
 *   Shift B (Evening) · up 14d 6h    (current shift + system uptime)
 * Shift is computed client-side from the DB-backed config
 * (GET /api/settings/shifts). Uptime comes from /health.
 */
function SidebarClock() {
  const { is24h } = useTimeFormat();
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const shiftCfg = useQuery({
    queryKey: ["settings-shifts"],
    queryFn: () => api.get<ShiftsConfig>("/settings/shifts").catch(() => undefined),
    refetchInterval: 300_000,
    staleTime: 300_000,
    retry: false,
  });

  const health = useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: async () => {
      const res = await fetch("/health");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as HealthResponse;
    },
    refetchInterval: 10_000,
    retry: false,
  });

  const shift = currentShift(shiftCfg.data, now);

  const timeStr = now.toLocaleTimeString(undefined, {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: !is24h,
  });
  const weekday = now.toLocaleDateString(undefined, { weekday: "long" });
  const dd = String(now.getDate()).padStart(2, "0");
  const mon = now.toLocaleDateString(undefined, { month: "short" });
  const dateStr = `${weekday}, ${dd}-${mon}-${now.getFullYear()}`;
  const wk = isoWeek(now);

  return (
    <div
      className="mt-auto px-4 py-3 flex flex-col items-center text-center leading-tight"
      style={{ borderTop: "0.5px solid var(--separator)" }}
    >
      <span
        className="text-2xl font-semibold tabular-nums"
        style={{ color: "var(--text-primary)", letterSpacing: "-0.02em" }}
      >
        {timeStr}
      </span>
      <span className="text-[11px] mt-1" style={{ color: "var(--text-secondary)" }}>
        {dateStr} (WK {wk})
      </span>
      <span className="text-[11px] mt-0.5" style={{ color: "var(--text-secondary)" }}>
        {shift ? (
          <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>
            Shift {shift.code} ({shift.label})
          </span>
        ) : (
          <span>No shift</span>
        )}
        {health.data && <>{" · up "}{formatUptimeShort(health.data.uptime_sec)}</>}
      </span>
    </div>
  );
}

/**
 * Notification bell with a live active-alarm count badge. Polls
 * /api/alarms/active (same source the Alarms page uses) every 10s and
 * shows the count as a red badge. Click navigates to the Alarms page.
 * Distinct from the dashboard's alarm cards — this is the always-present
 * cross-page signal.
 */
function NotificationBell() {
  const navigate = useNavigate();
  // Same queryKey + queryFn as Nav's alarm badge so TanStack Query dedupes
  // them into ONE shared poll (no duplicate /alarms/active traffic).
  const active = useQuery({
    queryKey: ["alarms-active"],
    queryFn: () => api.get<unknown[]>("/alarms/active").catch(() => []),
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
    staleTime: 0,
    retry: false,
  });
  const count = Array.isArray(active.data) ? active.data.length : 0;

  return (
    <button
      onClick={() => navigate("/alarms")}
      className="relative flex items-center justify-center rounded-full transition-colors hover:opacity-80"
      style={{ width: 32, height: 32 }}
      title={count > 0 ? `${count} active alarm${count === 1 ? "" : "s"}` : "No active alarms"}
      aria-label="Alarms"
    >
      <Bell className="h-5 w-5" style={{ color: "var(--text-secondary)" }} />
      {count > 0 && (
        <span
          className="absolute flex items-center justify-center rounded-full text-white font-semibold"
          style={{
            top: 0,
            right: 0,
            minWidth: 16,
            height: 16,
            padding: "0 4px",
            fontSize: 10,
            lineHeight: 1,
            backgroundColor: "var(--ios-red)",
          }}
        >
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  );
}

/**
 * Account menu (Phase 21, redesigned). Avatar circle with the user's initial,
 * username, and a chevron. Click opens a small dropdown: identity header
 * (username + role), Change password, Sign out. Closes on outside-click /
 * Escape. Lightweight — no dropdown dependency.
 */
function UserMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!user) return null;

  const initial = user.username.charAt(0).toUpperCase();
  const roleLabel = user.role.charAt(0).toUpperCase() + user.role.slice(1);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-full pl-1 pr-2 py-1 transition-colors"
        style={{ backgroundColor: open ? "var(--bg-grouped)" : "transparent" }}
        title="Account"
      >
        <span
          className="flex items-center justify-center rounded-full text-white text-xs font-semibold"
          style={{ width: 26, height: 26, backgroundColor: "var(--ios-blue)" }}
        >
          {initial}
        </span>
        <span
          className="hidden sm:block text-sm font-medium"
          style={{ color: "var(--text-primary)" }}
        >
          {user.username}
        </span>
        <ChevronDown className="h-3.5 w-3.5" style={{ color: "var(--text-secondary)" }} />
      </button>

      {open && (
        <div
          className="absolute right-0 mt-2 w-56 rounded-xl py-1.5 z-50"
          style={{
            backgroundColor: "var(--bg-elevated)",
            border: "0.5px solid var(--separator)",
            boxShadow: "0 8px 28px rgba(0,0,0,0.16)",
          }}
        >
          {/* Identity header */}
          <div className="px-3 py-2" style={{ borderBottom: "0.5px solid var(--separator)" }}>
            <div className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              {user.username}
            </div>
            <div className="mt-0.5">
              <Badge variant="outline" className="text-[10px] uppercase tracking-wide">
                {roleLabel}
              </Badge>
            </div>
          </div>

          {/* Actions */}
          <button
            onClick={() => { setOpen(false); navigate("/account/password"); }}
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left transition-colors hover:opacity-80"
            style={{ color: "var(--text-primary)" }}
          >
            <KeyRound className="h-4 w-4" style={{ color: "var(--text-secondary)" }} />
            Change password
          </button>
          <button
            onClick={() => { setOpen(false); logout(); }}
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left transition-colors hover:opacity-80"
            style={{ color: "var(--ios-red)" }}
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function formatUptime(sec: number): string {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
