/**
 * Phase 23 — per-control role gating.
 *
 * Centralizes "what can this user do" so individual controls don't scatter
 * raw hasRole("engineer") checks (which drift from the backend over time).
 *
 * The capability map mirrors the backend RBAC policy (rbac_middleware.py):
 *
 *   view       -> viewer    reads, dashboards, trends, diagnostics
 *   operate    -> operator  acknowledge alarms, write command/setpoint tags
 *   configure  -> engineer  create/edit/delete devices, tags, blocks, alarms,
 *                           calc blocks, OPC sources, logging config
 *   administer -> admin     users, API keys, system settings
 *
 * Usage:
 *   const can = useCan();
 *   can("configure")              // boolean
 *
 *   <Gate cap="configure">        // renders children only if allowed
 *     <Button>Add tag</Button>
 *   </Gate>
 *
 *   <Gate cap="configure" mode="disable">   // renders but disables + tooltip
 *     <Button onClick={...}>Save</Button>
 *   </Gate>
 */
import { type ReactNode, cloneElement, isValidElement } from "react";
import { useAuth, type Role } from "@/lib/auth";

export type Capability = "view" | "operate" | "configure" | "administer";

// Capability -> minimum role. Mirrors backend rbac_middleware._required_role.
const CAP_ROLE: Record<Capability, Role> = {
  view: "viewer",
  operate: "operator",
  configure: "engineer",
  administer: "admin",
};

const ROLE_LABEL: Record<Role, string> = {
  viewer: "Viewer",
  operator: "Operator",
  engineer: "Engineer",
  admin: "Admin",
};

/** Hook: returns can(cap) -> boolean. */
export function useCan() {
  const { hasRole } = useAuth();
  return (cap: Capability): boolean => hasRole(CAP_ROLE[cap]);
}

/** The minimum role label for a capability (for tooltips/messages). */
export function requiredRoleLabel(cap: Capability): string {
  return ROLE_LABEL[CAP_ROLE[cap]];
}

type GateProps = {
  cap: Capability;
  children: ReactNode;
  /**
   * "hide" (default): render nothing when not allowed.
   * "disable": render children but disabled, with a tooltip explaining the
   *            required role. Only works when the single child accepts
   *            `disabled` (e.g. Button, input).
   */
  mode?: "hide" | "disable";
  /** Optional fallback shown in hide mode when not allowed. */
  fallback?: ReactNode;
};

/**
 * Gate a control by capability. In "disable" mode the child is rendered but
 * disabled with a native tooltip ("Requires Engineer role or higher"), so the
 * user understands WHY rather than getting a surprise 403 or a vanished button.
 */
export function Gate({ cap, children, mode = "hide", fallback = null }: GateProps) {
  const can = useCan();
  const allowed = can(cap);

  if (allowed) return <>{children}</>;

  if (mode === "disable" && isValidElement(children)) {
    const title = `Requires ${requiredRoleLabel(cap)} role or higher`;
    // Wrap so the tooltip shows even though the control is disabled
    // (disabled elements don't fire hover events in some browsers).
    return (
      <span title={title} style={{ display: "inline-flex", cursor: "not-allowed" }}>
        {cloneElement(children as any, { disabled: true })}
      </span>
    );
  }

  return <>{fallback}</>;
}
