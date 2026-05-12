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
 */
import { NavLink, Outlet } from "react-router";
import { cn } from "@/lib/utils";

const tabs = [
  { to: "/global/engineering-units", label: "Engineering Units" },
  { to: "/global/groups", label: "Groups" },
  { to: "/global/named-sets", label: "Enumerations" },
];

export default function GlobalLayout() {
  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Global Reference Data</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Cross-cutting masters that tags reference: engineering units (with
          146 seeded SI/IEC entries), tag groups, and enumeration state
          machines (boolean + integer value-to-label mappings).
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
