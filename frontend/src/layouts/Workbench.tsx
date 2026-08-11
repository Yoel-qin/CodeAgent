import { useEffect } from "react";
import { Layout, Menu, Input, Space, Tag, Typography, theme } from "antd";
import {
  MessageOutlined,
  FolderOutlined,
  FileTextOutlined,
  ApartmentOutlined,
  SyncOutlined,
  RobotOutlined,
  WarningOutlined,
  DashboardOutlined,
  SettingOutlined,
  SearchOutlined,
  BarChartOutlined,
} from "@ant-design/icons";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAppStore } from "../stores/app";
import ContextPanel from "../components/ContextPanel";
import CommandPalette from "../components/CommandPalette";
import { useHotkey } from "../hooks/useHotkey";

const { Header, Sider, Content, Footer } = Layout;
const { Text } = Typography;

const navItems = [
  {
    key: "workspace",
    label: "工作区",
    type: "group" as const,
    children: [
      { key: "/chat", label: "智能问答", icon: <MessageOutlined /> },
      { key: "/code", label: "代码浏览", icon: <FolderOutlined /> },
      { key: "/documents", label: "文档管理", icon: <FileTextOutlined /> },
      { key: "/graph", label: "知识图谱", icon: <ApartmentOutlined /> },
    ],
  },
  {
    key: "admin",
    label: "管理",
    type: "group" as const,
    children: [
      { key: "/sync", label: "同步管理", icon: <SyncOutlined /> },
      { key: "/agents", label: "Agent 面板", icon: <RobotOutlined /> },
      { key: "/staleness", label: "腐化审批", icon: <WarningOutlined /> },
      { key: "/monitor", label: "系统监控", icon: <DashboardOutlined /> },
      { key: "/eval", label: "检索评测", icon: <BarChartOutlined /> },
      { key: "/settings", label: "系统设置", icon: <SettingOutlined /> },
    ],
  },
];

export default function Workbench() {
  const location = useLocation();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const health = useAppStore((s) => s.health);
  const fetchHealth = useAppStore((s) => s.fetchHealth);
  const setCmdkOpen = useAppStore((s) => s.setCmdkOpen);
  useHotkey();

  useEffect(() => {
    fetchHealth();
    const t = setInterval(fetchHealth, 30_000);
    return () => clearInterval(t);
  }, [fetchHealth]);

  const selected = "/" + (location.pathname.split("/")[1] || "chat");
  const ok = health?.status === "healthy";

  return (
    <Layout style={{ height: "100vh" }}>
      <Header
        style={{
          height: 56,
          lineHeight: "56px",
          padding: "0 16px",
          display: "flex",
          alignItems: "center",
          gap: 16,
          background: token.colorBgContainer,
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <Space size={8}>
          <span style={{ fontSize: 18 }}>🚀</span>
          <Text strong>CodeRAG 知识库</Text>
        </Space>
        <Input
          prefix={<SearchOutlined style={{ color: token.colorTextQuaternary }} />}
          placeholder="全局搜索…  (⌘K)"
          style={{ maxWidth: 420, marginLeft: 16, cursor: "pointer" }}
          readOnly
          onClick={() => setCmdkOpen(true)}
        />
        <div style={{ flex: 1 }} />
        <Tag color={ok ? "success" : health ? "warning" : "default"}>
          {ok ? "● 索引正常" : health ? "● 部分降级" : "● 连接中"}
        </Tag>
        <RobotOutlined style={{ fontSize: 16, cursor: "pointer" }} />
        <SettingOutlined
          style={{ fontSize: 16, cursor: "pointer" }}
          onClick={() => navigate("/settings")}
        />
      </Header>

      <Layout>
        <Sider width={240} theme="dark" style={{ overflow: "auto" }}>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[selected]}
            items={navItems}
            onClick={({ key }) => navigate(key)}
            style={{ borderRight: 0, paddingTop: 8 }}
          />
        </Sider>

        <Content style={{ overflow: "auto", background: token.colorBgLayout }}>
          <Outlet />
        </Content>

        <Sider width={320} theme="light" style={{ borderLeft: `1px solid ${token.colorBorderSecondary}` }}>
          <ContextPanel />
        </Sider>
      </Layout>

      <Footer style={{ height: 28, padding: "0 16px", lineHeight: "28px", fontSize: 12, background: token.colorBgContainer }}>
        <Space size={24}>
          <Text type="secondary">CodeRAG · Phase 0 脚手架</Text>
          <Text type="secondary">env: {health?.env ?? "—"}</Text>
          <Text type="secondary">
            组件：{health ? Object.entries(health.components).map(([k, v]) => `${k}:${v ? "✓" : "✗"}`).join(" ") : "—"}
          </Text>
        </Space>
      </Footer>
      <CommandPalette />
    </Layout>
  );
}
