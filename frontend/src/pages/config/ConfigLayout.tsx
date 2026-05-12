/**
 * Configuration editor layout — shared tab bar across Channels / Devices /
 * Register Blocks. The actual content for each tab is a nested route
 * rendered into the <Outlet />. /config redirects to /config/channels.
 *
 * Phase 8.5: Engineering Units, Groups, and Named Sets moved to /global —
 * they're cross-cutting reference data, not protocol-level setup.
 */
import { NavLink, Outlet } from "react-router";
import { cn } from "@/lib/utils";

const tabs = [
  { to: "/config/channels", label: "Channels" },
  { to: "/config/devices", label: "Devices" },
  { to: "/config/blocks", label: "Register Blocks" },
];

export default function ConfigLayout() {
  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Configuration</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Edit channels, devices, and register blocks. Changes propagate to
          workers within ~10 seconds via the Phase 3.5 hot-reload.
        </p>
      </div>

      <div className="flex gap-1 border-b">
        {tabs.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            className={({ isActive }) =>
              cn(
                "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
                isActive
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )
            }
          >
            {t.label}
          </NavLink>
        ))}
      </div>

      <Outlet />
    </div>
  );
}
