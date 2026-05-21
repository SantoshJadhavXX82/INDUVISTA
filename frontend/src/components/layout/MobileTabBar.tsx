/**
 * Phase 19 — MobileTabBar.
 *
 * Bottom tab bar for mobile (sidebar replacement under 768px). Shows
 * the 4 most-used destinations as iOS-style tabs with icon + label.
 *
 * The "More" tab opens a sheet that lists everything else from the
 * sidebar (Health, Audit, Gaps, Devices, Calc tags, Modbus, Global/
 * Setup) — operators rarely need those on a phone, so they live behind
 * one extra tap.
 *
 * Active state matches iOS: filled icon variant + iOS-blue color,
 * label switches to iOS-blue too. Inactive: outline icon, muted gray.
 *
 * Sticks to the bottom of the viewport with safe-area inset for iPhone
 * notch / home indicator. Backdrop-blurred surface (subtle iOS feel).
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, useLocation } from "react-router";
import {
  Gauge, TrendingUp, BellRing, Bell, Tag as TagIcon, MoreHorizontal,
  HeartPulse, FileText, LineChart, Cpu, Sigma, Wrench, SlidersHorizontal,
  X, type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";


// 4 primary tabs + More
interface TabItem {
  to: string;
  label: string;
  icon: LucideIcon;
  iconActive?: LucideIcon;
  matchPrefix?: string;
}

const PRIMARY_TABS: TabItem[] = [
  { to: "/dashboard", label: "Dashboard", icon: Gauge },
  { to: "/alarms",    label: "Alarms",    icon: Bell, iconActive: BellRing, matchPrefix: "/alarms" },
  { to: "/tags",      label: "Tags",      icon: TagIcon, matchPrefix: "/tags" },
  { to: "/trend",     label: "Trend",     icon: TrendingUp, matchPrefix: "/trend" },
];

const MORE_ITEMS: { section: string; items: { to: string; label: string; icon: LucideIcon }[] }[] = [
  {
    section: "Diagnose",
    items: [
      { to: "/diagnostics", label: "Health", icon: HeartPulse },
      { to: "/audit-log",   label: "Audit",  icon: FileText },
      { to: "/data-gaps",   label: "Gaps",   icon: LineChart },
    ],
  },
  {
    section: "Configure",
    items: [
      { to: "/config/devices",         label: "Devices",   icon: Cpu },
      { to: "/global/calc-blocks",     label: "Calc tags", icon: Sigma },
      { to: "/modbus/registers",       label: "Modbus",    icon: Wrench },
      { to: "/global/engineering-units", label: "Global/Setup", icon: SlidersHorizontal },
    ],
  },
];


export default function MobileTabBar() {
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);

  // Active alarm count for the badge — same query as Sidebar/Dashboard
  const alarms = useQuery({
    queryKey: ["alarms-active"],
    queryFn: () => api.get<unknown[]>("/alarms/active").catch(() => []),
    refetchInterval: 5_000,
    staleTime: 0,
    retry: false,
  });
  const alarmCount = Array.isArray(alarms.data) ? alarms.data.length : 0;

  return (
    <>
      {moreOpen && (
        <MoreSheet
          activePath={location.pathname}
          onClose={() => setMoreOpen(false)}
        />
      )}

      <nav
        className="fixed bottom-0 left-0 right-0 z-40 flex items-stretch"
        style={{
          backgroundColor: "color-mix(in oklab, var(--bg-elevated) 92%, transparent)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          borderTop: "0.5px solid var(--separator)",
          paddingBottom: "env(safe-area-inset-bottom, 0)",
        }}
        aria-label="Primary navigation"
      >
        {PRIMARY_TABS.map((tab) => {
          const isAlarms = tab.to === "/alarms";
          const Icon = isAlarms && alarmCount > 0 && tab.iconActive
            ? tab.iconActive
            : tab.icon;
          return (
            <TabButton
              key={tab.to}
              to={tab.to}
              label={tab.label}
              icon={Icon}
              alarming={isAlarms && alarmCount > 0}
              badge={isAlarms && alarmCount > 0 ? alarmCount : undefined}
              matchPrefix={tab.matchPrefix}
            />
          );
        })}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          className="flex-1 flex flex-col items-center justify-center gap-0.5 py-2"
          style={{ color: "var(--text-secondary)" }}
          aria-label="More"
        >
          <MoreHorizontal style={{ width: 22, height: 22 }} strokeWidth={2} />
          <span className="text-[10px] font-medium">More</span>
        </button>
      </nav>
    </>
  );
}


function TabButton({
  to, label, icon: Icon, alarming = false, badge, matchPrefix,
}: {
  to: string;
  label: string;
  icon: LucideIcon;
  alarming?: boolean;
  badge?: number;
  matchPrefix?: string;
}) {
  return (
    <NavLink
      to={to}
      end={!matchPrefix}
      className="flex-1 flex flex-col items-center justify-center gap-0.5 py-2 relative"
    >
      {({ isActive }) => (
        <>
          <Icon
            style={{
              width: 22,
              height: 22,
              color: alarming
                ? "var(--ios-red)"
                : isActive
                  ? "var(--ios-blue)"
                  : "var(--text-secondary)",
            }}
            fill={(isActive || alarming) ? "currentColor" : "none"}
            fillOpacity={(isActive || alarming) ? 0.18 : undefined}
            strokeWidth={isActive ? 2.2 : 2}
            className={cn(alarming && "induvista-bell-shake")}
          />
          <span
            className="text-[10px] font-medium"
            style={{
              color: alarming
                ? "var(--ios-red)"
                : isActive
                  ? "var(--ios-blue)"
                  : "var(--text-secondary)",
            }}
          >
            {label}
          </span>
          {badge !== undefined && badge > 0 && (
            <span
              className="absolute text-[9px] font-semibold tabular-nums"
              style={{
                top: 4,
                right: "calc(50% - 18px)",
                backgroundColor: "var(--ios-red)",
                color: "#fff",
                borderRadius: 999,
                padding: "0 5px",
                minWidth: 16,
                height: 14,
                lineHeight: "14px",
                textAlign: "center",
              }}
            >
              {badge > 99 ? "99+" : badge}
            </span>
          )}
        </>
      )}
    </NavLink>
  );
}


function MoreSheet({
  activePath, onClose,
}: { activePath: string; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0"
        style={{ backgroundColor: "rgba(0,0,0,0.35)" }}
      />
      {/* Sheet */}
      <div
        className="relative"
        style={{
          backgroundColor: "var(--bg-elevated)",
          borderTopLeftRadius: 16,
          borderTopRightRadius: 16,
          paddingBottom: "env(safe-area-inset-bottom, 12px)",
          maxHeight: "75vh",
          overflow: "auto",
          animation: "slide-up 0.22s cubic-bezier(0.4,0,0.2,1)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Pull indicator */}
        <div className="flex justify-center pt-2 pb-1">
          <div style={{ width: 36, height: 4, borderRadius: 999, backgroundColor: "var(--ios-gray-3)" }} />
        </div>

        <div className="flex items-center justify-between px-4 py-2">
          <h2 className="text-[17px] font-semibold tracking-tight" style={{ color: "var(--text-primary)" }}>
            More
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-full"
            style={{ backgroundColor: "var(--ios-gray-5)", color: "var(--text-secondary)" }}
            aria-label="Close"
          >
            <X style={{ width: 16, height: 16 }} strokeWidth={2.5} />
          </button>
        </div>

        <div className="px-4 pb-4 space-y-4">
          {MORE_ITEMS.map((sec) => (
            <div key={sec.section}>
              <div
                className="px-3 mb-1 text-[11px] font-semibold uppercase tracking-wider"
                style={{ color: "var(--text-secondary)" }}
              >
                {sec.section}
              </div>
              <div
                style={{
                  backgroundColor: "var(--bg-grouped)",
                  borderRadius: 12,
                }}
              >
                {sec.items.map((item, idx) => {
                  const active = activePath.startsWith(item.to);
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      onClick={onClose}
                      className="flex items-center gap-3 px-3 py-3 text-[15px]"
                      style={{
                        color: active ? "var(--ios-blue)" : "var(--text-primary)",
                        borderBottom: idx < sec.items.length - 1
                          ? "0.5px solid var(--separator)"
                          : "none",
                      }}
                    >
                      <item.icon
                        style={{ width: 20, height: 20, color: active ? "var(--ios-blue)" : "var(--ios-gray-1)" }}
                        strokeWidth={2}
                      />
                      <span>{item.label}</span>
                    </NavLink>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
