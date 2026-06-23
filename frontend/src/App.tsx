import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";
import AppShell from "@/components/layout/AppShell";
import { TimeFormatProvider } from "@/lib/timeFormat";
import { PageSkeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/toaster";
// Phase 21 - auth gate
import Login from "@/pages/Login";
import RequireAuth from "@/components/RequireAuth";
const ChangePassword = lazy(() => import("@/pages/ChangePassword"));
const Users = lazy(() => import("@/pages/Users"));
const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Diagnostics = lazy(() => import("@/pages/Diagnostics"));
const TagExplorer = lazy(() => import("@/pages/TagExplorer"));
const FrameInspector = lazy(() => import("@/pages/FrameInspector"));
const RegisterBrowser = lazy(() => import("@/pages/RegisterBrowser"));
const DataGaps = lazy(() => import("@/pages/DataGaps"));
const Historian = lazy(() => import("@/pages/Historian"));
const Reports = lazy(() => import("@/pages/Reports"));
const Explorer = lazy(() => import("@/pages/Explorer"));
const ReportsConfig = lazy(() => import("@/pages/ReportsConfig"));
const About = lazy(() => import("@/pages/About"));
const Help = lazy(() => import("@/pages/Help"));
const Writes = lazy(() => import("@/pages/Writes"));
const WriteConsole = lazy(() => import("@/pages/WriteConsole"));
const ConfigLayout = lazy(() => import("@/pages/config/ConfigLayout"));
const Channels = lazy(() => import("@/pages/config/Channels"));
const Devices = lazy(() => import("@/pages/config/Devices"));
const RegisterBlocks = lazy(() => import("@/pages/config/RegisterBlocks"));
const GlobalLayout = lazy(() => import("@/pages/global/GlobalLayout"));
const EngineeringUnits = lazy(() => import("@/pages/EngineeringUnits"));
const Groups = lazy(() => import("@/pages/Groups"));
const NamedSets = lazy(() => import("@/pages/NamedSets"));
const DutyStandbyValues = lazy(() => import("@/pages/global/DutyStandbyValues"));
// Phase 27d MVP — General Settings (timezone picker)
const Settings = lazy(() => import("@/pages/Settings"));
const ModbusLayout = lazy(() => import("@/pages/modbus/ModbusLayout"));
// Phase 13.2 — Trend module
const Trend = lazy(() => import("@/pages/Trend"));
// Phase 14.5 - Alarms module
const Alarms = lazy(() => import("@/pages/Alarms"));
const AlarmSeveritiesAdmin = lazy(() => import("@/pages/AlarmSeveritiesAdmin"));
const AlarmRuleTypesAdmin = lazy(() => import("@/pages/AlarmRuleTypesAdmin"));
// Phase 15.3 / 16.0b - Calc blocks admin
const CalcDefinitionsAdmin = lazy(() => import("@/pages/CalcDefinitionsAdmin"));
// Phase 16.0g - Audit log viewer
const AuditLog = lazy(() => import("@/pages/AuditLog"));
// Phase OPC-web.3 — backend-managed OPC UA sources page
const OpcSources = lazy(() => import("@/pages/OpcSources"));

export default function App() {
  return (
    <TimeFormatProvider>
      <Routes>
        {/* Phase 21 - login lives OUTSIDE the shell (no nav/header). */}
        <Route path="/login" element={<Login />} />
        {/* Everything else requires authentication. */}
        <Route
          path="/*"
          element={
            <RequireAuth>
              <AppShell>
                <AuthedRoutes />
              </AppShell>
            </RequireAuth>
          }
        />
      </Routes>
      <Toaster />
    </TimeFormatProvider>
  );
}

function AuthedRoutes() {
  return (
    <Suspense fallback={<PageSkeleton />}>
    <Routes>
        <Route path="/" element={<Navigate to="/diagnostics" replace />} />
        <Route path="/diagnostics" element={<Diagnostics />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/account/password" element={<ChangePassword />} />
        <Route
          path="/global/users"
          element={<RequireAuth minRole="admin"><Users /></RequireAuth>}
        />
        <Route path="/tags" element={<TagExplorer />} />
        <Route path="/data-gaps" element={<DataGaps />} />
        <Route path="/historian" element={<Historian />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/explorer" element={<Explorer />} />
        <Route path="/reports/config" element={<ReportsConfig />} />
        {/* Triggers + Destinations were consolidated into Report Config tabs.
            Keep the old paths working as redirects into the right tab. */}
        <Route path="/config/report-triggers" element={<Navigate to="/reports/config?tab=triggers" replace />} />
        <Route path="/config/report-destinations" element={<Navigate to="/reports/config?tab=destinations" replace />} />
        <Route path="/about" element={<About />} />
        <Route path="/help" element={<Help />} />
        {/* Phase 13.2 — Trend module (historical first, real-time in 13.3) */}
        <Route path="/trend" element={<Trend />} />
        {/* Phase 14.5 - Alarms */}
        <Route path="/alarms" element={<Alarms />} />
        <Route path="/global/alarm-severities" element={<AlarmSeveritiesAdmin />} />
        <Route path="/global/alarm-types" element={<AlarmRuleTypesAdmin />} />
        {/* Phase 15.3 / 16.0b — Calc blocks admin */}
        <Route path="/global/calc-blocks" element={<CalcDefinitionsAdmin />} />
        {/* Phase 16.0g — Audit log viewer */}
        <Route path="/audit-log" element={<AuditLog />} />

        {/* Phase OPC-web.3 — OPC UA sources management */}
        <Route path="/config/opc-sources" element={<OpcSources />} />

        {/* Phase 8.5 — Modbus TCP/IP tools grouped under /modbus */}
        <Route path="/modbus" element={<ModbusLayout />}>
          <Route index element={<Navigate to="/modbus/frames" replace />} />
          <Route path="frames" element={<FrameInspector />} />
          <Route path="registers" element={<RegisterBrowser />} />
          <Route path="write-console" element={<WriteConsole />} />
          <Route path="write-audit" element={<Writes />} />
        </Route>

        {/* Back-compat — old top-level routes redirect into the Modbus group */}
        <Route path="/frames" element={<Navigate to="/modbus/frames" replace />} />
        <Route path="/registers" element={<Navigate to="/modbus/registers" replace />} />
        <Route path="/writes" element={<Navigate to="/modbus/write-audit" replace />} />

        {/* Phase 8.5 — Global reference data grouped under /global */}
        <Route path="/global" element={<GlobalLayout />}>
          <Route index element={<Navigate to="/global/engineering-units" replace />} />
          <Route path="engineering-units" element={<EngineeringUnits />} />
          <Route path="groups" element={<Groups />} />
          <Route path="named-sets" element={<NamedSets />} />
          <Route path="duty-standby-values" element={<DutyStandbyValues />} />
          {/* Phase 27d MVP — General Settings (timezone picker) */}
          <Route path="settings" element={<Settings />} />
        </Route>

        {/* Back-compat — old /config/* paths for Global moved out */}
        <Route path="/config/engineering-units" element={<Navigate to="/global/engineering-units" replace />} />
        <Route path="/config/groups" element={<Navigate to="/global/groups" replace />} />
        <Route path="/config/named-sets" element={<Navigate to="/global/named-sets" replace />} />

        {/* Configuration — channels / devices / blocks */}
        <Route path="/config" element={<ConfigLayout />}>
          <Route index element={<Navigate to="/config/channels" replace />} />
          <Route path="channels" element={<Channels />} />
          <Route path="devices" element={<Devices />} />
          <Route path="blocks" element={<RegisterBlocks />} />
        </Route>
    </Routes>
    </Suspense>
  );
}
