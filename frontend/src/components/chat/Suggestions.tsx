import { useEffect, useState } from "react";
import { Typography, theme } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { postSuggestions } from "../../api/conversations";

/** 回答下方的追问建议：点击即作为新问题发送。 */
export default function Suggestions({
  conversationId,
  lastMessageId,
  disabled,
  onPick,
}: {
  conversationId: string;
  lastMessageId: string;
  disabled: boolean;
  onPick: (text: string) => void;
}) {
  const { token } = theme.useToken();
  const [items, setItems] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    setItems([]);
    postSuggestions(conversationId, lastMessageId)
      .then((s) => {
        if (alive) setItems(s);
      })
      .catch(() => {
        if (alive) setItems([]);
      });
    return () => {
      alive = false;
    };
  }, [conversationId, lastMessageId]);

  if (!items.length) return null;

  return (
    <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
      <Typography.Text type="secondary" style={{ fontSize: 11, width: "100%" }}>
        <QuestionCircleOutlined /> 追问建议
      </Typography.Text>
      {items.map((s, i) => (
        <button
          key={i}
          disabled={disabled}
          onClick={() => onPick(s)}
          style={{
            padding: "4px 10px",
            borderRadius: 14,
            border: `1px solid ${token.colorBorderSecondary}`,
            background: token.colorBgContainer,
            color: token.colorText,
            fontSize: 12,
            cursor: disabled ? "not-allowed" : "pointer",
            opacity: disabled ? 0.5 : 1,
          }}
        >
          {s}
        </button>
      ))}
    </div>
  );
}
