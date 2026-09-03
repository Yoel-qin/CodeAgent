import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Workbench from "./layouts/Workbench";
import ChatPage from "./pages/ChatPage";
import SyncPage from "./pages/SyncPage";
import DocumentsPage from "./pages/DocumentsPage";
import GraphPage from "./pages/GraphPage";

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
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
