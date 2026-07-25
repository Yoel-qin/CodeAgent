import { useEffect, useState } from "react";
import { Drawer, Tag, Typography, Spin, Empty, theme } from "antd";
import { CodeOutlined, FileTextOutlined } from "@ant-design/icons";
import { getRetrieval, type RetrievalDetail } from "../../api/conversations";

const CHANNEL_LABEL: Record<string, string> = {
  vector: "向量语义",
  bm25: "BM25 词法",
  graph_traverse: "图遍历",
  graph_vector: "图向量",
};

/** 检索详情抽屉：展开某条 assistant 消息的三阶段召回漏斗与精排候选。 */
export default function RetrievalDrawer({
  messageId,
  open,
  onClose,
}: {
  messageId: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const { token } = theme.useToken();
  const [data, setData] = useState<RetrievalDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !messageId) return;
    setLoading(true);
    setData(null);
    getRetrieval(messageId)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [open, messageId]);

  const Pill = (label: string, n: number | null | undefined, color: string) => (
    <Tag color={color} style={{ marginRight: 6 }}>
      {label} {n ?? 0}
    </Tag>
  );

  return (
    <Drawer title="检索详情" open={open} onClose={onClose} width={460}>
      <Spin spinning={loading}>
        {!data ? (
          !loading && <Empty description="无检索详情" />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {/* Stage 1 */}
            <section>
              <Typography.Title level={5} style={{ marginBottom: 8 }}>
                Stage 1 · 召回 + RRF 融合
                {data.stage1.latency_ms != null && (
                  <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
                    {data.stage1.latency_ms} ms
                  </Typography.Text>
                )}
              </Typography.Title>
              <div style={{ marginBottom: 6 }}>
                {data.stage1.channels.map((ch) => Pill(CHANNEL_LABEL[ch.name] ?? ch.name, ch.count, "blue"))}
              </div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                RRF 融合去重：{data.stage1.merged_count} 条
                {data.stage1.terms?.length ? ` · 检索词：${data.stage1.terms.join(", ")}` : ""}
              </Typography.Text>
            </section>

            {/* Stage 2 */}
            <section>
              <Typography.Title level={5} style={{ marginBottom: 8 }}>Stage 2 · 粗排</Typography.Title>
              {data.stage2.model ? (
                <Typography.Text style={{ fontSize: 13 }}>
                  {data.stage2.model} → <b>{data.stage2.output_count ?? 0}</b> 条
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  未启用（单阶段精排）
                </Typography.Text>
              )}
            </section>

            {/* Stage 3 */}
            <section>
              <Typography.Title level={5} style={{ marginBottom: 8 }}>
                Stage 3 · 精排
                {data.stage3.latency_ms != null && (
                  <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
                    {data.stage3.latency_ms} ms
                  </Typography.Text>
                )}
              </Typography.Title>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {data.stage3.model} · {data.stage3.rerank_on ? "精排生效" : "未启用（RRF 排序）"} · 最终 {data.stage3.output_count} 条
              </Typography.Text>
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
                {data.stage3.results.map((r, i) => {
                  const isCode = r.type === "code";
                  return (
                    <div
                      key={r.chunk_id + i}
                      style={{
                        padding: "8px 10px",
                        borderRadius: 6,
                        background: token.colorFillQuaternary,
                        border: `1px solid ${token.colorBorderSecondary}`,
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <Tag color={isCode ? "blue" : "gold"} icon={isCode ? <CodeOutlined /> : <FileTextOutlined />} style={{ margin: 0 }}>
                          {isCode ? "代码" : "文档"}
                        </Tag>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {r.score != null ? r.score.toFixed(3) : ""}
                        </Typography.Text>
                      </div>
                      <Typography.Text ellipsis style={{ display: "block", marginTop: 4, fontSize: 12 }} className="code-font">
                        {r.label ?? (isCode ? `${r.class}.${r.method}` : (r.path ?? []).join(" > "))}
                      </Typography.Text>
                    </div>
                  );
                })}
              </div>
            </section>
          </div>
        )}
      </Spin>
    </Drawer>
  );
}
