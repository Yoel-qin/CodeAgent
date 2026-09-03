import { useEffect, useState } from "react";
import { Button, List, Typography, theme, message as antMessage } from "antd";
import { PlusOutlined, MessageOutlined } from "@ant-design/icons";
import { listConversations, type ConversationItem } from "../../api/conversations";

/** 左侧会话列表：新建 / 切换 / 历史回显（v2：裸数组，无 message_count → 副行只显时间）。 */
export default function ConversationList({
  activeId,
  onSelect,
  onNew,
}: {
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const { token } = theme.useToken();
  const [items, setItems] = useState<ConversationItem[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      setItems(await listConversations());
    } catch {
      antMessage.warning("会话列表加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", borderRight: `1px solid ${token.colorBorderSecondary}` }}>
      <div style={{ padding: "10px 12px", borderBottom: `1px solid ${token.colorBorderSecondary}` }}>
        <Button type="primary" block icon={<PlusOutlined />} onClick={onNew}>
          新建会话
        </Button>
      </div>
      <div style={{ flex: 1, overflow: "auto" }}>
        <List
          loading={loading}
          dataSource={items}
          locale={{ emptyText: "暂无会话" }}
          renderItem={(it) => {
            const active = it.id === activeId;
            return (
              <div
                onClick={() => onSelect(it.id)}
                style={{
                  padding: "10px 12px",
                  cursor: "pointer",
                  background: active ? token.colorPrimaryBg : "transparent",
                  borderLeft: active ? `3px solid ${token.colorPrimary}` : "3px solid transparent",
                  borderBottom: `1px solid ${token.colorBorderSecondary}`,
                }}
              >
                <Typography.Text ellipsis style={{ display: "block", fontSize: 13, fontWeight: active ? 600 : 400 }}>
                  <MessageOutlined style={{ marginRight: 6, color: token.colorTextTertiary }} />
                  {it.title}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {new Date(it.updated_at).toLocaleString()}
                </Typography.Text>
              </div>
            );
          }}
        />
      </div>
    </div>
  );
}
