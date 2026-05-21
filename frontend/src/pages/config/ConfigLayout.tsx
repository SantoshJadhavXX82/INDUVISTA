/**
 * Configuration editor layout — shared tab bar across Channels / Devices /
 * Register Blocks. The actual content for each tab is a nested route
 * rendered into the <Outlet />. /config redirects to /config/channels.
 *
 * Phase 8.5: Engineering Units, Groups, and Named Sets moved to /global —
 * they're cross-cutting reference data, not protocol-level setup.
 *
 * Phase 18 — refactored to use PageHeader + iOS-style segmented tab strip
 * (same pattern as Tag Explorer's All/Pair tabs). Each top-level Configure
 * leaf in the sidebar still lands you on the matching tab; this layout is
 * effectively the shared chrome.
 */
import { NavLink, Outlet } from "react-router";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/ui/page-header";

const tabs = [
  { to: "/config/channels", label: "Networks" },
  { to: "/config/devices", label: "Devices" },
  { to: "/config/blocks", label: "Register blocks" },
];

export default function ConfigLayout() {
  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <PageHeader
        title="Configuration"
        subtitle="Edit networks, devices, and register blocks. Changes propagate to workers within ~10 seconds."
      />

      <div
        className="flex gap-1 p-1 rounded-lg w-fit"
        style={{ backgroundColor: "var(--ios-gray-5)" }}
      >
        {tabs.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            className={() =>
              cn("px-4 py-1.5 text-[13px] font-medium rounded-md transition-colors")
            }
            style={({ isActive }) => isActive
              ? { backgroundColor: "var(--bg-elevated)", color: "var(--text-primary)" }
              : { color: "var(--text-secondary)" }
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
