import { Navigate, Route, Routes } from "react-router";
import AppShell from "@/components/layout/AppShell";
import Dashboard from "@/pages/Dashboard";
import Diagnostics from "@/pages/Diagnostics";
import TagExplorer from "@/pages/TagExplorer";
import FrameInspector from "@/pages/FrameInspector";
import RegisterBrowser from "@/pages/RegisterBrowser";
import DataGaps from "@/pages/DataGaps";
import Writes from "@/pages/Writes";
import WriteConsole from "@/pages/WriteConsole";
import ConfigLayout from "@/pages/config/ConfigLayout";
import Channels from "@/pages/config/Channels";
import Devices from "@/pages/config/Devices";
import RegisterBlocks from "@/pages/config/RegisterBlocks";
import GlobalLayout from "@/pages/global/GlobalLayout";
import EngineeringUnits from "@/pages/EngineeringUnits";
import Groups from "@/pages/Groups";
import NamedSets from "@/pages/NamedSets";
import DutyStandbyValues from "@/pages/global/DutyStandbyValues";
import ModbusLayout from "@/pages/modbus/ModbusLayout";
// Phase 13.2 — Trend module
import Trend from "@/pages/Trend";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/diagnostics" replace />} />
        <Route path="/diagnostics" element={<Diagnostics />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/tags" element={<TagExplorer />} />
        <Route path="/data-gaps" element={<DataGaps />} />
        {/* Phase 13.2 — Trend module (historical first, real-time in 13.3) */}
        <Route path="/trend" element={<Trend />} />

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
    </AppShell>
  );
}
