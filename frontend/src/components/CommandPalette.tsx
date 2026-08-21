/**
 * 全局命令面板（⌘K）：关键词搜索知识库（/v1/search）+ 快捷页面导航。
 * 打开由 Zustand cmdkOpen 控制（useHotkey 监听 ⌘K/Ctrl+K，或点 Workbench 顶部触发框）。
 * 选中知识库命中 → setFocused 写入右侧 ContextPanel 已读的 focused；选中导航 → useNavigate。
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Input, List, Modal, Tag, Typography, theme } from "antd";
import { CodeOutlined, FileTextOutlined, RightOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { useAppStore } from "../stores/app";
import { searchKb, type SearchItem } from "../api/search";

const { Text } = Typography;

interface Cmd {
  key: string;
  label: string;
}

const NAV_COMMANDS: Cmd[] = [
  { key: "/chat", label: "智能问答" },
  { key: "/documents", label: "文档管理" },
  { key: "/graph", label: "知识图谱" },
  { key: "/sync", label: "同步管理" },
  { key: "/agents", label: "Agent 面板" },
  { key: "/staleness", label: "腐化审批" },
  { key: "/monitor", label: "系统监控" },
  { key: "/eval", label: "检索评测" },
];

type Entry = { kind: "nav"; cmd: Cmd } | { kind: "kb"; item: SearchItem };

export default function CommandPalette() {
  const open = useAppStore((s) => s.cmdkOpen);
  const setOpen = useAppStore((s) => s.setCmdkOpen);
  const setFocused = useAppStore((s) => s.setFocused);
  const navigate = useNavigate();
  const { token } = theme.useToken();

  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState(0);
  const inputRef = useRef<{ focus: () => void } | null>(null);

  // 打开时重置 + 聚焦输入框
  useEffect(() => {
    if (open) {
      setQ("");
      setResults([]);
      setSel(0);
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [open]);

  // 防抖搜索（200ms）
  useEffect(() => {
    if (!q.trim()) {
      setResults([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const r = await searchKb(q.trim());
        setResults(r.items);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  const navFiltered = NAV_COMMANDS.filter((c) => !q.trim() || c.label.includes(q.trim()));
  const entries: Entry[] = [
    ...navFiltered.map((cmd) => ({ kind: "nav" as const, cmd })),
    ...results.map((item) => ({ kind: "kb" as const, item })),
  ];

  useEffect(() => {
    setSel(0);
  }, [q]);

  const activate = (e: Entry) => {
    if (e.kind === "nav") {
      navigate(e.cmd.key);
    } else {
      setFocused({ chunk_id: e.item.chunk_id, type: e.item.kind, label: e.item.label });
    }
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
        placeholder="搜索代码 / 文档 / 跳转页面…"
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
        {q.trim() && (
          <List
            header={<Text type="secondary" style={{ fontSize: 12 }}>知识库（{results.length}）</Text>}
            loading={loading}
            dataSource={results}
            renderItem={(item, idx) => {
              const flatIdx = navFiltered.length + idx;
              return (
                <List.Item
                  style={{ cursor: "pointer", padding: "8px 16px", background: rowBg(flatIdx) }}
                  onMouseEnter={() => setSel(flatIdx)}
                  onClick={() => activate({ kind: "kb", item })}
                >
                  <List.Item.Meta
                    avatar={item.kind === "code" ? <CodeOutlined /> : <FileTextOutlined />}
                    title={<Text>{item.label}</Text>}
                    description={
                      <Text type="secondary" ellipsis style={{ fontSize: 12 }}>
                        {item.snippet}
                      </Text>
                    }
                  />
                  <Tag>{item.kind === "code" ? "代码" : "文档"}</Tag>
                </List.Item>
              );
            }}
            locale={{ emptyText: loading ? "搜索中…" : "无匹配" }}
          />
        )}
        <List
          header={<Text type="secondary" style={{ fontSize: 12 }}>导航</Text>}
          dataSource={navFiltered}
          renderItem={(cmd, idx) => (
            <List.Item
              style={{ cursor: "pointer", padding: "8px 16px", background: rowBg(idx) }}
              onMouseEnter={() => setSel(idx)}
              onClick={() => activate({ kind: "nav", cmd })}
            >
              <Text>{cmd.label}</Text>
              <RightOutlined style={{ fontSize: 10, color: token.colorTextQuaternary, marginLeft: 8 }} />
            </List.Item>
          )}
        />
      </div>
    </Modal>
  );
}
