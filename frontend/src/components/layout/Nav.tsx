/**
 * Phase 18 — Sidebar redesign.
 *
 * Why this changed:
 *   - 21 items across 4 sections felt cluttered. Operators couldn't see
 *     daily-use entries without scrolling.
 *   - The "Setup" section was 7 items of global reference data used
 *     rarely (per week or month, not daily). It dominated the sidebar.
 *   - Modbus tools (Frames / Registers / Write Console / Write Audit)
 *     are a related cluster used together; they belong grouped.
 *
 * Restructure:
 *   - 11 top-level items spread over 3 sections (Operate / Diagnose /
 *     Configure).
 *   - Two expandable groups (Modbus, Reference) that collapse 11 items
 *     down to 2 sidebar rows when not in use.
 *   - Alarms row carries an unread-count badge when alarms are active.
 *
 * Visual:
 *   - iOS-blue active state (`--ios-blue-soft` background + `--ios-blue`
 *     text), no left-border accents.
 *   - Section labels in muted uppercase tracking.
 *   - Active count badge for alarms in iOS red.
 *
 * Phase 27d MVP — added "General" entry under Global/Setup for the new
 * plant-wide settings page (timezone now, more to come).
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, useLocation } from "react-router";
import {
  Gauge, TrendingUp, BellRing, Bell, Tag as TagIcon,
  HeartPulse, FileText, LineChart, Database,
  Cpu, Sigma, Wrench, SlidersHorizontal,
  ChevronDown, ChevronRight,
  ScanLine, Radio, Zap, FileClock,
  Ruler, Palette, ListChecks, Tag, Hash, ArrowLeftRight, Network, ListTree,
  // Phase OPC-web.3 — OPC UA sources nav icon
  Wifi,
  Settings,
  UserCog,
  FileBarChart,
  HelpCircle, Info,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";  // Phase 21 - admin-only Users link


type Leaf = {
  kind: "leaf";
  to: string;
  label: string;
  icon: LucideIcon;
  /** Optional URL prefix for active matching when nested routes apply. */
  matchPrefix?: string;
  /** Slot for a trailing badge (e.g. unread alarm count). */
  badge?: React.ReactNode;
  /** Optional class applied only to the icon — used by the Alarms entry
   *  to apply the bell-shake keyframes animation when alarms are firing. */
  iconClassName?: string;
  /** Optional inline style for the icon (e.g. iOS-red color when alarming). */
  iconStyle?: React.CSSProperties;
  /** Phase 21 - only render for admin users. */
  adminOnly?: boolean;
};

type ExpandableGroup = {
  kind: "group";
  id: string;
  label: string;
  icon: LucideIcon;
  matchPrefix: string;
  children: Leaf[];
};

type Section = {
  kind: "section";
  label: string;
  children: (Leaf | ExpandableGroup)[];
};


function useEntries(alarmCount: number): Section[] {
  return useMemo<Section[]>(() => [
    {
      kind: "section",
      label: "Operate",
      children: [
        { kind: "leaf", to: "/dashboard", label: "Dashboard", icon: Gauge },
        { kind: "leaf", to: "/trend",     label: "Trend",     icon: TrendingUp, matchPrefix: "/trend" },
        { kind: "leaf", to: "/reports",   label: "Reports",   icon: FileBarChart, matchPrefix: "/reports" },
        {
          kind: "leaf", to: "/alarms", label: "Alarms",
          // When alarms are firing, swap the static Bell for the animated
          // BellRing in iOS-red — the wiggle (induvista-bell-shake class)
          // and color shift give peripheral-vision-grade attention without
          // being annoying.
          icon: alarmCount > 0 ? BellRing : Bell,
          iconClassName: alarmCount > 0 ? "induvista-bell-shake" : undefined,
          iconStyle: alarmCount > 0 ? { color: "var(--ios-red)" } : undefined,
          matchPrefix: "/alarms",
          badge: alarmCount > 0 ? <AlarmBadge count={alarmCount} /> : undefined,
        },
        { kind: "leaf", to: "/tags", label: "Tags", icon: TagIcon, matchPrefix: "/tags" },
      ],
    },
    {
      kind: "section",
      label: "Diagnose",
      children: [
        { kind: "leaf", to: "/diagnostics", label: "Health", icon: HeartPulse },
        { kind: "leaf", to: "/audit-log",   label: "Audit",  icon: FileText, matchPrefix: "/audit-log" },
        { kind: "leaf", to: "/data-gaps",   label: "Gaps",   icon: LineChart },
        { kind: "leaf", to: "/historian",   label: "Historian", icon: Database, matchPrefix: "/historian" },
      ],
    },
    {
      kind: "section",
      label: "Configure",
      children: [
        { kind: "leaf", to: "/config/channels", label: "Networks",         icon: Network,    matchPrefix: "/config/channels" },
        { kind: "leaf", to: "/config/devices",  label: "Devices",          icon: Cpu,        matchPrefix: "/config/devices" },
        { kind: "leaf", to: "/config/blocks",   label: "Register blocks",  icon: ListTree,   matchPrefix: "/config/blocks" },
        { kind: "leaf", to: "/global/calc-blocks", label: "Calc tags",     icon: Sigma, matchPrefix: "/global/calc-blocks" },
        // Phase OPC-web.3 — OPC UA sources management. Sits in Configure
        // because it's a per-deployment data-source setup (like Networks
        // and Devices), not global reference data.
        { kind: "leaf", to: "/config/opc-sources", label: "OPC UA",        icon: Wifi,  matchPrefix: "/config/opc-sources" },
        {
          kind: "group", id: "modbus", label: "Modbus", icon: Wrench,
          matchPrefix: "/modbus", children: [
            { kind: "leaf", to: "/modbus/registers",     label: "Registers", icon: ScanLine,  matchPrefix: "/modbus/registers" },
            { kind: "leaf", to: "/modbus/frames",        label: "Frames",    icon: Radio,     matchPrefix: "/modbus/frames" },
            { kind: "leaf", to: "/modbus/write-console", label: "Write",     icon: Zap,       matchPrefix: "/modbus/write-console" },
            { kind: "leaf", to: "/modbus/write-audit",   label: "Audit",     icon: FileClock, matchPrefix: "/modbus/write-audit" },
          ],
        },
        {
          // Renamed from "Reference" — operators called this Global/Setup
          // since it holds the cross-product reference data (units, severities,
          // groups, etc.) used to define and classify tags. Networks and
          // Register blocks moved up to top-level Configure entries because
          // they're per-deployment infrastructure, not global vocabulary.
          //
          // Phase 27d MVP added "General" at the end — plant-wide settings
          // (timezone now, plant name / units / shift definition later).
          // Sits after Duty/standby because it's plant-wide config rather
          // than reference-data masters like the others.
          kind: "group", id: "global-setup", label: "Global/Setup", icon: SlidersHorizontal,
          matchPrefix: "/global", children: [
            { kind: "leaf", to: "/global/engineering-units",   label: "Units",         icon: Ruler,          matchPrefix: "/global/engineering-units" },
            { kind: "leaf", to: "/global/alarm-severities",    label: "Severities",    icon: Palette,        matchPrefix: "/global/alarm-severities" },
            { kind: "leaf", to: "/global/alarm-types",         label: "Alarm types",   icon: ListChecks,     matchPrefix: "/global/alarm-types" },
            { kind: "leaf", to: "/global/groups",              label: "Groups",        icon: Tag,            matchPrefix: "/global/groups" },
            { kind: "leaf", to: "/global/named-sets",          label: "Enumerations",  icon: Hash,           matchPrefix: "/global/named-sets" },
            { kind: "leaf", to: "/global/duty-standby-values", label: "Duty/standby",  icon: ArrowLeftRight, matchPrefix: "/global/duty-standby-values" },
            { kind: "leaf", to: "/global/settings",            label: "General",       icon: Settings,       matchPrefix: "/global/settings" },
            { kind: "leaf", to: "/global/users",               label: "Users",         icon: UserCog,        matchPrefix: "/global/users", adminOnly: true },
          ],
        },
      ],
    },
    {
      kind: "section",
      label: "Help",
      children: [
        { kind: "leaf", to: "/help",  label: "Help",  icon: HelpCircle, matchPrefix: "/help" },
        { kind: "leaf", to: "/about", label: "About", icon: Info,       matchPrefix: "/about" },
      ],
    },
  ], [alarmCount]);
}


export default function Nav() {
  const location = useLocation();
  const { hasRole } = useAuth();
  const isAdmin = hasRole("admin");

  // Phase 18 fix — use the SAME query as the Alarms page so React Query
  // dedupes the fetch (one HTTP call shared by Alarms page + Dashboard +
  // sidebar). The /alarms/active endpoint returns list[AlarmActive] directly,
  // not {alarms: [...]} or {count: N}. Count is just the array length.
  const alarms = useQuery({
    queryKey: ["alarms-active"],
    queryFn: () => api.get<unknown[]>("/alarms/active").catch(() => []),
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
    staleTime: 0,
    retry: false,
  });
  const alarmCount = Array.isArray(alarms.data) ? alarms.data.length : 0;

  const entries = useEntries(alarmCount);

  return (
    <nav className="nav-autohide flex flex-col gap-3 p-2 flex-1 overflow-y-auto min-h-0">
      {entries.map((section, i) => (
        <SectionBlock key={`s-${i}`} section={section} activePath={location.pathname} isAdmin={isAdmin} />
      ))}
    </nav>
  );
}


function SectionBlock({
  section, activePath, isAdmin,
}: { section: Section; activePath: string; isAdmin: boolean }) {
  return (
    <div>
      <div
        className="px-3 mb-1 text-[10px] font-semibold uppercase tracking-wider"
        style={{ color: "var(--text-secondary)" }}
      >
        {section.label}
      </div>
      <div className="flex flex-col gap-0.5">
        {section.children
          .filter((c) => c.kind !== "leaf" || !c.adminOnly || isAdmin)
          .map((c) =>
            c.kind === "leaf"
              ? <LeafLink key={c.to} item={c} />
              : <ExpandableBlock key={c.id} group={c} activePath={activePath} isAdmin={isAdmin} />,
          )}
      </div>
    </div>
  );
}


const NAV_TILE_COLORS: Record<string, string> = {
  "/dashboard": "blue",
  "/reports": "indigo",
  "/trend": "teal",
  "/alarms": "red",
  "/tags": "green",
  "/diagnostics": "pink",
  "/audit-log": "gray",
  "/data-gaps": "orange",
  "/historian": "indigo",
  "/config/channels": "teal",
  "/config/devices": "blue",
  "/config/blocks": "indigo",
  "/global/calc-blocks": "purple",
  "/config/opc-sources": "teal",
  "/modbus/registers": "blue",
  "/modbus/frames": "indigo",
  "/modbus/write-console": "orange",
  "/modbus/write-audit": "gray",
  "/global/engineering-units": "green",
  "/global/alarm-severities": "purple",
  "/global/alarm-types": "indigo",
  "/global/groups": "green",
  "/global/named-sets": "teal",
  "/global/duty-standby-values": "orange",
  "/global/settings": "gray",
  "/global/users": "blue",
  "/help": "blue",
  "/about": "gray",
};

function tileColor(to: string): string {
  return NAV_TILE_COLORS[to] ?? "gray";
}

function LeafLink({ item, nested = false }: { item: Leaf; nested?: boolean }) {
  return (
    <NavLink
      to={item.to}
      end={!item.matchPrefix}
      className={() =>
        cn(
          "flex items-center gap-3 rounded-md text-[13px] transition-colors",
          nested ? "pl-8 pr-3 py-1" : "px-3 py-1.5",
        )
      }
      style={({ isActive }) => isActive
        ? {
            backgroundColor: "var(--ios-blue-soft)",
            color: "var(--ios-blue-on-soft)",
            fontWeight: 500,
          }
        : { color: "var(--text-secondary)" }
      }
    >
      {({ isActive }) => (
        <>
          {/* Phase 18 polish — filled-on-active: when this nav item is the
              current route, the icon gets fill=currentColor (becomes a
              "weight increase" you can feel in peripheral vision). When
              inactive it stays clean outline. iOS Settings does this. */}
          <span
            className="shrink-0 inline-flex items-center justify-center rounded-[7px]"
            style={{
              width: 28,
              height: 28,
              backgroundColor: item.iconStyle?.color
                ? "transparent"
                : isActive
                  ? `var(--ios-${tileColor(item.to)})`
                  : `var(--ios-${tileColor(item.to)}-soft)`,
            }}
          >
            <item.icon
              className={cn("h-5 w-5", item.iconClassName)}
              style={
                item.iconStyle ?? {
                  color: isActive ? "#fff" : `var(--ios-${tileColor(item.to)})`,
                }
              }
              strokeWidth={2}
            />
          </span>
          <span className="flex-1 truncate">{item.label}</span>
          {item.badge}
        </>
      )}
    </NavLink>
  );
}


function ExpandableBlock({
  group, activePath, isAdmin,
}: { group: ExpandableGroup; activePath: string; isAdmin: boolean }) {
  // Phase 18 refinement — auto-expand when any of the group's CHILDREN
  // would be highlighted as active, not just when the parent matchPrefix
  // matches. This matters when sibling top-level leafs share a URL prefix
  // with the group (e.g. Calc tags lives at /global/calc-blocks but is no
  // longer a child of Global/Setup, so the group shouldn't auto-open
  // when Calc tags is selected).
  const childIsActive = group.children.some((c) =>
    c.matchPrefix
      ? activePath.startsWith(c.matchPrefix)
      : activePath === c.to,
  );
  const [forceOpen, setForceOpen] = useState(false);
  const open = childIsActive || forceOpen;

  return (
    <div>
      <button
        type="button"
        onClick={() => setForceOpen(o => !o)}
        className="w-full flex items-center gap-3 px-3 py-1.5 rounded-md text-[13px] transition-colors"
        style={childIsActive
          ? { color: "var(--ios-blue-on-soft)", fontWeight: 500 }
          : { color: "var(--text-secondary)" }}
      >
        <group.icon
          className="h-4 w-4 shrink-0"
          fill={childIsActive ? "currentColor" : "none"}
          fillOpacity={childIsActive ? 0.18 : undefined}
          strokeWidth={childIsActive ? 2 : 1.75}
        />
        <span className="flex-1 text-left truncate">{group.label}</span>
        {open
          ? <ChevronDown className="h-3.5 w-3.5 opacity-60" />
          : <ChevronRight className="h-3.5 w-3.5 opacity-60" />}
      </button>
      {open && (
        <div className="flex flex-col gap-0.5 mt-0.5 mb-1">
          {group.children
            .filter((c) => !c.adminOnly || isAdmin)
            .map((c) => (
              <LeafLink key={c.to} item={c} nested />
            ))}
        </div>
      )}
    </div>
  );
}


function AlarmBadge({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <span
      className="text-[10px] font-semibold tabular-nums px-1.5 rounded-full"
      style={{
        backgroundColor: "var(--ios-red)",
        color: "#fff",
        minWidth: 18,
        height: 16,
        lineHeight: "16px",
        textAlign: "center",
        display: "inline-block",
      }}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}
