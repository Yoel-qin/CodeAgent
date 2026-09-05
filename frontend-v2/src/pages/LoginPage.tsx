import { useState } from "react";
import { Button, Card, Form, Input, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";
import { login } from "../api/auth";

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const nav = useNavigate();

  const onFinish = async (vals: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = await login(vals);
      localStorage.setItem("coderag_username", res.user.username);
      message.success(`欢迎，${res.user.username}（${res.user.role}）`);
      nav("/chat");
    } catch (e) {
      message.error(`登录失败：${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Card title="CodeRAG-v2 登录" style={{ width: 360 }}>
        <Typography.Paragraph type="secondary">
          系统已开启 RBAC，请登录后使用。
        </Typography.Paragraph>
        <Form layout="vertical" onFinish={(v) => void onFinish(v)}>
          <Form.Item name="username" label="用户名"
                     rules={[{ required: true, message: "请输入用户名" }]}>
            <Input autoFocus />
          </Form.Item>
          <Form.Item name="password" label="密码"
                     rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
