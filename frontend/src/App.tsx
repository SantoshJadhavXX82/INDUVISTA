import { Navigate, Route, Routes } from "react-router";
import AppShell from "@/components/layout/AppShell";
import Dashboard from "@/pages/Dashboard";
import Diagnostics from "@/pages/Diagnostics";
import TagExplorer from "@/pages/TagExplorer";
import DataGaps from "@/pages/DataGaps";
import ConfigLayout from "@/pages/config/ConfigLayout";
import Channels from "@/pages/config/Channels";
import Devices from "@/pages/config/Devices";
import RegisterBlocks from "@/pages/config/RegisterBlocks";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/diagnostics" replace />} />
        <Route path="/diagnostics" element={<Diagnostics />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/tags" element={<TagExplorer />} />
        <Route path="/data-gaps" element={<DataGaps />} />

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
