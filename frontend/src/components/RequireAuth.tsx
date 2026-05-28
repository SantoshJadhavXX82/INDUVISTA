/**
 * Route gate (Phase 21). Redirects unauthenticated users to /login,
 * preserving the attempted path so login can send them back.
 *
 * Optional minRole gates a subtree by role (e.g. wrap config routes in
 * <RequireAuth minRole="engineer">). Insufficient role shows a 403 notice
 * rather than redirecting (the user IS logged in, just not allowed).
 */
import { Navigate, useLocation } from "react-router";
import { useAuth, type Role } from "@/lib/auth";

export default function RequireAuth({
  children,
  minRole,
}: {
  children: React.ReactNode;
  minRole?: Role;
}) {
  const { isAuthenticated, hasRole } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  if (minRole && !hasRole(minRole)) {
    return (
      <div style={{ padding: "2rem", textAlign: "center", color: "var(--text-secondary)" }}>
        <h2 style={{ marginBottom: "0.5rem", color: "var(--text-primary)" }}>
          Access denied
        </h2>
        <p>This page requires the <strong>{minRole}</strong> role or higher.</p>
      </div>
    );
  }
  return <>{children}</>;
}
