import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Workbench from "./layouts/Workbench";
import ChatPage from "./pages/ChatPage";
import PlaceholderPage from "./pages/PlaceholderPage";

const P = (title: string, stage: string, desc?: string) => (
  <PlaceholderPage title={title} stage={stage} desc={desc} />
);

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Workbench />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="code" element={P("代码浏览", "Phase 1 (P1)", "按模块/包/类树形浏览。")} />
          <Route path="documents" element={P("文档管理", "Phase 1.5d (P1)", "PDF/Word 上传、解析进度、预览。")} />
          <Route path="graph" element={P("知识图谱", "Phase 4 (P2)", "调用图 / 代码-文档关联图 / 模块依赖图。")} />
          <Route path="communities" element={P("社区总览", "Phase 6 (P2)", "GraphRAG 社区摘要浏览。")} />
          <Route path="sync" element={P("同步管理", "Phase 8 (P1)", "增量同步任务与回滚记录。")} />
          <Route path="agents" element={P("Agent 面板", "Phase 7 (P3)", "各 Agent 状态与历史任务。")} />
          <Route path="monitor" element={P("系统监控", "Phase 8 (P3)", "检索性能、显存、API 用量。")} />
          <Route path="settings" element={P("系统设置", "Phase 1 (P3)", "模型配置、API Key 管理。")} />
          <Route path="*" element={P("未知页面", "—")} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
