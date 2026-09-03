import { Drawer, Tag, Typography, Empty, theme } from "antd";
import type { AgentStep, RetrievalInfo } from "../../hooks/types";

/** 紧凑展示工具入参：key=value（字符串原样、其余 JSON 化），避免长串撑爆抽屉。 */
const fmtArgs = (args: Record<string, unknown>) =>
  Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");

/** Agent 轨迹抽屉（props 驱动，不 fetch）：steps 来自消息 state / 历史 meta.agent_steps。 */
export default function RetrievalDrawer({
  open,
  onClose,
  steps,
  retrieval,
}: {
  open: boolean;
  onClose: () => void;
  steps: AgentStep[] | null;
  retrieval: RetrievalInfo | null;
}) {
  const { token } = theme.useToken();

  return (
    <Drawer title="Agent 轨迹" open={open} onClose={onClose} width={460}>
      {!steps?.length && !retrieval ? (
        <Empty description="无检索详情" />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* 检索摘要（retrieval 事件 / meta.route 派生） */}
          {retrieval && (
            <section>
              <Typography.Title level={5} style={{ marginBottom: 8 }}>检索摘要</Typography.Title>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                <Tag color="geekblue" style={{ margin: 0 }}>mode: {retrieval.mode}</Tag>
                {retrieval.intent && <Tag color="purple" style={{ margin: 0 }}>intent: {retrieval.intent}</Tag>}
                {retrieval.confidence != null && (
                  <Tag color="cyan" style={{ margin: 0 }}>置信度 {retrieval.confidence.toFixed(2)}</Tag>
                )}
                <Tag color="blue" style={{ margin: 0 }}>代码命中 {retrieval.code_hits ?? 0}</Tag>
                <Tag color="gold" style={{ margin: 0 }}>文档命中 {retrieval.doc_hits ?? 0}</Tag>
              </div>
              {!!retrieval.tools?.length && (
                <Typography.Text type="secondary" style={{ display: "block", marginTop: 8, fontSize: 12 }}>
                  工具：{retrieval.tools.join(", ")}
                </Typography.Text>
              )}
            </section>
          )}

          {/* Agent 工具调用轨迹 */}
          {!!steps?.length && (
            <section>
              <Typography.Title level={5} style={{ marginBottom: 8 }}>
                工具调用轨迹
                <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
                  {steps.length} 步
                </Typography.Text>
              </Typography.Title>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {steps.map((s, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 6,
                      background: token.colorFillQuaternary,
                      border: `1px solid ${token.colorBorderSecondary}`,
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <Tag color="purple" style={{ margin: 0 }}>{s.tool}</Tag>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {s.duration_ms != null ? `${s.duration_ms} ms` : `命中 ${s.n} 条`}
                      </Typography.Text>
                    </div>
                    {Object.keys(s.args).length > 0 && (
                      <Typography.Text
                        ellipsis
                        className="code-font"
                        style={{ display: "block", marginTop: 4, fontSize: 12 }}
                      >
                        {fmtArgs(s.args)}
                      </Typography.Text>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </Drawer>
  );
}
