import { useEffect } from "react";
import { Button, Dropdown, Layout, Menu, Input, Space, Tag, Typography, theme } from "antd";
import {
  MessageOutlined,
  FileTextOutlined,
  ApartmentOutlined,
  SyncOutlined,
  SearchOutlined,
  RobotOutlined,
  DashboardOutlined,
  LineChartOutlined,
} from "@ant-design/icons";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { clearToken, getToken } from "../api/client";
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
      { key: "/documents", label: "文档管理", icon: <FileTextOutlined /> },
      { key: "/graph", label: "调用图", icon: <ApartmentOutlined /> },
    ],
  },
  {
    key: "admin",
    label: "管理",
    type: "group" as const,
    children: [
      { key: "/sync", label: "同步管道", icon: <SyncOutlined /> },
      { key: "/monitor", label: "系统监控", icon: <DashboardOutlined /> },
      { key: "/eval", label: "评测", icon: <LineChartOutlined /> },
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

  // M9 RBAC 守卫：后端要求登录且本地无 token → 回登录页（health 未加载时放行，
  // 首帧不闪跳；health 拉到 auth_required 后本守卫立即生效）。
  // 置于 useEffect 之后——守卫提前返回会跳过其后 hook 调用，违反 Rules of Hooks。
  if (health?.auth_required && !getToken()) {
    return <Navigate to="/login" replace />;
  }

  const selected = "/" + (location.pathname.split("/")[1] || "chat");
  const ok = health?.status === "ok";

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
        {health?.auth_required && localStorage.getItem("coderag_username") ? (
          <Dropdown
            menu={{
              items: [{ key: "logout", label: "登出" }],
              onClick: ({ key }) => {
                if (key === "logout") {
                  clearToken();
                  localStorage.removeItem("coderag_username");
                  window.location.href = "/login";
                }
              },
            }}
          >
            <Button type="text" size="small">
              {localStorage.getItem("coderag_username")}
            </Button>
          </Dropdown>
        ) : null}
        <RobotOutlined style={{ fontSize: 16, cursor: "pointer" }} />
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
          <Text type="secondary">CodeRAG-v2 · Agentic 检索</Text>
          <Text type="secondary">
            组件：
            {health
              ? Object.entries(health.components)
                  .map(([k, v]) => `${k}:${v.status === "ok" ? "✓" : "✗"}`)
                  .join(" ")
              : "—"}
          </Text>
        </Space>
      </Footer>
      <CommandPalette />
    </Layout>
  );
}
