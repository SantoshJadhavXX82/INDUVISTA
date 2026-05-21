/**
 * "Global" reference-data layout — Engineering Units, Groups, Enumerations.
 *
 * These three are cross-cutting masters used by tags across all channels/
 * devices/blocks. They were nested under /config before, which made
 * Configuration's tab strip overflow on narrow screens. Splitting them out
 * into their own /global section keeps Configuration focused on protocol-
 * level setup (channels, devices, blocks).
 *
 * Naming note: what the UI calls "Enumerations" is stored in the DB as
 * `named_sets` (and the API stays /api/named-sets). We renamed the UI label
 * because "Named Sets" collides with DeltaV's identical concept — keeping
 * vocabulary distinct avoids confusion during DeltaV-side integration work.
 *
 * Phase 18 — PageHeader + iOS segmented tabs.
 */
import { NavLink, Outlet } from "react-router";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/ui/page-header";

const tabs = [
  { to: "/global/engineering-units", label: "Units" },
  { to: "/global/groups", label: "Groups" },
  { to: "/global/named-sets", label: "Enumerations" },
  { to: "/global/duty-standby-values", label: "Duty/standby" },
];

export default function GlobalLayout() {
  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <PageHeader
        title="Global / Setup"
        subtitle="Cross-product reference data — engineering units, tag groups, enumeration state machines, duty/standby values. Used by tags across every device."
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
