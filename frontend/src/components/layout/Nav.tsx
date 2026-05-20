/**
 * Sidebar navigation — Phase 11 reorganization (Trend added in 13.3).
 * Phase 16.0e — Calc Blocks leaf added under Setup, alongside alarm admin.
 */
import { NavLink, useLocation } from "react-router";
import { cn } from "@/lib/utils";
import {
  Activity,
  Gauge,
  ListTree,
  Settings,
  AlertCircle,
  Radio,
  ScanLine,
  FileClock,
  Zap,
  Globe2,
  Network,
  Ruler,
  Tag,
  Hash,
  ArrowLeftRight,
  ServerCog,
  Eye,
  TrendingUp,
  Palette,
  ListChecks,
  BellRing,
  Calculator,
  Shield,
  type LucideIcon,
} from "lucide-react";

type Leaf = {
  kind: "leaf";
  to: string;
  label: string;
  icon: LucideIcon;
  matchPrefix?: string;
};

type Section = {
  kind: "section";
  label: string;
  icon: LucideIcon;
  children: Leaf[];
};

type NavEntry = Leaf | Section;

const entries: NavEntry[] = [
  {
    kind: "section",
    label: "Setup",
    icon: Globe2,
    children: [
      { kind: "leaf", to: "/global/engineering-units", label: "Engineering Units", icon: Ruler, matchPrefix: "/global/engineering-units" },
      { kind: "leaf", to: "/global/alarm-severities", label: "Alarm Severities", icon: Palette, matchPrefix: "/global/alarm-severities" },
      { kind: "leaf", to: "/global/alarm-types", label: "Alarm Types", icon: ListChecks, matchPrefix: "/global/alarm-types" },
      { kind: "leaf", to: "/global/calc-blocks", label: "Calc Blocks", icon: Calculator, matchPrefix: "/global/calc-blocks" },
      { kind: "leaf", to: "/global/groups", label: "Groups", icon: Tag, matchPrefix: "/global/groups" },
      { kind: "leaf", to: "/global/named-sets", label: "Enumerations", icon: Hash, matchPrefix: "/global/named-sets" },
      { kind: "leaf", to: "/global/duty-standby-values", label: "Duty/Standby Values", icon: ArrowLeftRight, matchPrefix: "/global/duty-standby-values" },
    ],
  },
  {
    kind: "section",
    label: "Operate",
    icon: Gauge,
    children: [
      { kind: "leaf", to: "/dashboard", label: "Live Dashboard", icon: Gauge },
      { kind: "leaf", to: "/trend", label: "Trend", icon: TrendingUp, matchPrefix: "/trend" },
      { kind: "leaf", to: "/alarms", label: "Alarms", icon: BellRing, matchPrefix: "/alarms" },
      { kind: "leaf", to: "/audit-log", label: "Audit Log", icon: Shield, matchPrefix: "/audit-log" },
      { kind: "leaf", to: "/diagnostics", label: "Diagnostics", icon: Activity },
      { kind: "leaf", to: "/data-gaps", label: "Data Gaps", icon: AlertCircle },
    ],
  },
  {
    kind: "section",
    label: "Explore",
    icon: Eye,
    children: [
      { kind: "leaf", to: "/tags", label: "Tag Explorer", icon: ListTree, matchPrefix: "/tags" },
      { kind: "leaf", to: "/modbus/registers", label: "Register Browser", icon: ScanLine, matchPrefix: "/modbus/registers" },
      { kind: "leaf", to: "/modbus/frames", label: "Frame Inspector", icon: Radio, matchPrefix: "/modbus/frames" },
      { kind: "leaf", to: "/modbus/write-console", label: "Write Console", icon: Zap, matchPrefix: "/modbus/write-console" },
      { kind: "leaf", to: "/modbus/write-audit", label: "Write Audit", icon: FileClock, matchPrefix: "/modbus/write-audit" },
    ],
  },
  {
    kind: "section",
    label: "Configure",
    icon: Settings,
    children: [
      { kind: "leaf", to: "/config/channels", label: "Networks", icon: Network, matchPrefix: "/config/channels" },
      { kind: "leaf", to: "/config/devices", label: "Devices", icon: ServerCog, matchPrefix: "/config/devices" },
      { kind: "leaf", to: "/config/blocks", label: "Register Blocks", icon: ListTree, matchPrefix: "/config/blocks" },
    ],
  },
];

export default function Nav() {
  const location = useLocation();

  return (
    <nav className="flex flex-col gap-0.5 p-2">
      {entries.map((e, i) =>
        e.kind === "leaf" ? (
          <LeafLink key={e.to} item={e} />
        ) : (
          <SectionGroup
            key={`s-${i}`}
            section={e}
            activePath={location.pathname}
          />
        ),
      )}
    </nav>
  );
}

function LeafLink({ item }: { item: Leaf }) {
  return (
    <NavLink
      to={item.to}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
          isActive
            ? "bg-secondary text-secondary-foreground font-medium"
            : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
        )
      }
    >
      <item.icon className="h-4 w-4" />
      <span>{item.label}</span>
    </NavLink>
  );
}

function SectionGroup({
  section,
  activePath,
}: {
  section: Section;
  activePath: string;
}) {
  const anyActive = section.children.some((c) =>
    c.matchPrefix
      ? activePath.startsWith(c.matchPrefix)
      : activePath === c.to,
  );

  return (
    <div className="mt-2">
      <div
        className={cn(
          "flex items-center gap-2 px-3 py-1 text-[11px] uppercase tracking-wider",
          anyActive ? "text-foreground" : "text-muted-foreground/70",
        )}
      >
        <section.icon className="h-3 w-3" />
        <span className="font-semibold">{section.label}</span>
      </div>
      <div className="flex flex-col gap-0.5 mt-0.5">
        {section.children.map((c) => (
          <NavLink
            key={c.to}
            to={c.to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md ml-3 pl-3 pr-3 py-1.5 text-sm transition-colors border-l-2",
                isActive
                  ? "bg-secondary text-secondary-foreground font-medium border-l-foreground"
                  : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground border-l-transparent",
              )
            }
          >
            <c.icon className="h-3.5 w-3.5" />
            <span>{c.label}</span>
          </NavLink>
        ))}
      </div>
    </div>
  );
}
