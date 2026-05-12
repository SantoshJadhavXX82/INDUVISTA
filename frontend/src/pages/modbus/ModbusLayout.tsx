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
 */
import { NavLink, Outlet } from "react-router";
import { cn } from "@/lib/utils";

const tabs = [
  { to: "/modbus/frames", label: "Frame Inspector" },
  { to: "/modbus/registers", label: "Register Browser" },
  { to: "/modbus/write-console", label: "Write Console" },
  { to: "/modbus/write-audit", label: "Write Audit" },
];

export default function ModbusLayout() {
  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Modbus TCP/IP</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Live protocol tools: inspect captured frames, browse arbitrary
          register ranges, write to coils and holding registers, and review
          the write audit journal.
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
