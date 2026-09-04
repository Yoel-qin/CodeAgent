import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Workbench from "./layouts/Workbench";
import ChatPage from "./pages/ChatPage";
import SyncPage from "./pages/SyncPage";
import DocumentsPage from "./pages/DocumentsPage";
import GraphPage from "./pages/GraphPage";
import MonitorPage from "./pages/MonitorPage";
import EvalPage from "./pages/EvalPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Workbench />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="graph" element={<GraphPage />} />
          <Route path="sync" element={<SyncPage />} />
          <Route path="eval" element={<EvalPage />} />
          <Route path="monitor" element={<MonitorPage />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
