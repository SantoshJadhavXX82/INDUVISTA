/**
 * "Modbus" tools layout — Modbus-TCP/IP-specific features grouped together.
 *
 * Frame Inspector — captured request/response frames for debugging
 * Register Browser — ad-hoc range reads (FC 1-4)
 * Write Console   — interactive writes (FC 5/6/15/16)
 * Write Audit     — immutable journal of every write
 *
 * Reads happen automatically via the worker; this section is for the
 * interactive / forensic tools an engineer reaches for when commissioning
 * or troubleshooting a link.
 *
 * Phase 18 — PageHeader + iOS segmented tabs.
 */
import { NavLink, Outlet } from "react-router";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/ui/page-header";

const tabs = [
  { to: "/modbus/frames", label: "Frames" },
  { to: "/modbus/registers", label: "Registers" },
  { to: "/modbus/write-console", label: "Write" },
  { to: "/modbus/write-audit", label: "Audit" },
];

export default function ModbusLayout() {
  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <PageHeader
        title="Modbus TCP/IP"
        subtitle="Live protocol tools — inspect frames, browse register ranges, write to coils and holding registers, review the audit journal."
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
