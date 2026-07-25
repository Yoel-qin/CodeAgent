import { Result, Typography } from "antd";

interface Props {
  title: string;
  stage: string;
  desc?: string;
}

/** 通用占位页：尚未实现的模块路由统一指向这里。 */
export default function PlaceholderPage({ title, stage, desc }: Props) {
  return (
    <div style={{ padding: 24, height: "100%" }}>
      <Result
        status="info"
        title={title}
        subTitle={
          <Typography.Text type="secondary">
            计划在 <b>{stage}</b> 实现。{desc ? ` ${desc}` : ""}
          </Typography.Text>
        }
      />
    </div>
  );
}
