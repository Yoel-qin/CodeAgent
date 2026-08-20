import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Workbench from "./layouts/Workbench";
import LoginPage from "./pages/LoginPage";
import ChatPage from "./pages/ChatPage";
import SyncPage from "./pages/SyncPage";
import DocumentsPage from "./pages/DocumentsPage";
import GraphPage from "./pages/GraphPage";
import AgentsPage from "./pages/AgentsPage";
import StalenessPage from "./pages/StalenessPage";
import MonitorPage from "./pages/MonitorPage";
import EvalPage from "./pages/EvalPage";
import PlaceholderPage from "./pages/PlaceholderPage";

const P = (title: string, stage: string, desc?: string) => (
  <PlaceholderPage title={title} stage={stage} desc={desc} />
);

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<Workbench />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="code" element={P("代码浏览", "Phase 1 (P1)", "按模块/包/类树形浏览。")} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="graph" element={<GraphPage />} />
          <Route path="sync" element={<SyncPage />} />
          <Route path="agents" element={<AgentsPage />} />
          <Route path="staleness" element={<StalenessPage />} />
          <Route path="monitor" element={<MonitorPage />} />
          <Route path="eval" element={<EvalPage />} />
          <Route path="settings" element={P("系统设置", "Phase 1 (P3)", "模型配置、API Key 管理。")} />
          <Route path="*" element={P("未知页面", "—")} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
