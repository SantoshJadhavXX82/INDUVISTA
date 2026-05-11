import { NavLink } from "react-router";
import { cn } from "@/lib/utils";
import {
  Activity,
  Gauge,
  ListTree,
  Settings,
  AlertCircle,
} from "lucide-react";

type NavItem = {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** when true, the page is a placeholder and we visually tone it down */
  placeholder?: boolean;
};

const items: NavItem[] = [
  { to: "/diagnostics", label: "Diagnostics", icon: Activity },
  { to: "/dashboard", label: "Live Dashboard", icon: Gauge },
  { to: "/tags", label: "Tag Explorer", icon: ListTree },
  { to: "/config", label: "Configuration", icon: Settings },
  { to: "/data-gaps", label: "Data Gaps", icon: AlertCircle },
];

export default function Nav() {
  return (
    <nav className="flex flex-col gap-1 p-2">
      {items.map((it) => (
        <NavLink
          key={it.to}
          to={it.to}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
              isActive
                ? "bg-secondary text-secondary-foreground font-medium"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
              it.placeholder && "italic",
            )
          }
        >
          <it.icon className="h-4 w-4" />
          <span>{it.label}</span>
          {it.placeholder && (
            <span className="ml-auto text-[10px] uppercase tracking-wider text-muted-foreground">
              soon
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
