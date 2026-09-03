/**
 * 全局命令面板（⌘K）：纯页面导航（v2 无 /v1/search，知识库搜索组裁剪）。
 * 打开由 Zustand cmdkOpen 控制（useHotkey 监听 ⌘K/Ctrl+K，或点 Workbench 顶部触发框）。
 * 选中导航 → useNavigate。
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Input, List, Modal, Typography, theme } from "antd";
import { RightOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { useAppStore } from "../stores/app";

const { Text } = Typography;

interface Cmd {
  key: string;
  label: string;
}

const NAV_COMMANDS: Cmd[] = [
  { key: "/chat", label: "智能问答" },
  { key: "/documents", label: "文档管理" },
  { key: "/graph", label: "调用图" },
  { key: "/sync", label: "同步管道" },
];

export default function CommandPalette() {
  const open = useAppStore((s) => s.cmdkOpen);
  const setOpen = useAppStore((s) => s.setCmdkOpen);
  const navigate = useNavigate();
  const { token } = theme.useToken();

  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef<{ focus: () => void } | null>(null);

  const entries: Cmd[] = NAV_COMMANDS.filter((c) => !q.trim() || c.label.includes(q.trim()));

  // 打开时重置 + 聚焦输入框
  useEffect(() => {
    if (open) {
      setQ("");
      setSel(0);
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [open]);

  useEffect(() => {
    setSel(0);
  }, [q]);

  const activate = (e: Cmd) => {
    navigate(e.key);
    setOpen(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => Math.min(s + 1, Math.max(entries.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (entries[sel]) activate(entries[sel]);
    }
  };

  const rowBg = (i: number) => (i === sel ? token.colorPrimaryBg : undefined);

  return (
    <Modal
      open={open}
      onCancel={() => setOpen(false)}
      footer={null}
      centered
      width={640}
      closable={false}
      title={null}
    >
      <Input
        ref={inputRef as never}
        size="large"
        placeholder="跳转页面…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <div
        style={{
          marginTop: 8,
          maxHeight: 380,
          overflow: "auto",
          borderTop: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <List
          header={<Text type="secondary" style={{ fontSize: 12 }}>导航</Text>}
          dataSource={entries}
          renderItem={(cmd, idx) => (
            <List.Item
              style={{ cursor: "pointer", padding: "8px 16px", background: rowBg(idx) }}
              onMouseEnter={() => setSel(idx)}
              onClick={() => activate(cmd)}
            >
              <Text>{cmd.label}</Text>
              <RightOutlined style={{ fontSize: 10, color: token.colorTextQuaternary, marginLeft: 8 }} />
            </List.Item>
          )}
          locale={{ emptyText: "无匹配" }}
        />
      </div>
    </Modal>
  );
}
