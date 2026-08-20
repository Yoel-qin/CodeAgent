import { useState } from "react";
import { Button, Card, Form, Input, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "../stores/auth";

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const login = useAuthStore((s) => s.login);
  const navigate = useNavigate();

  const onFinish = async (v: { username: string; password: string }) => {
    setLoading(true);
    try {
      await login(v.username, v.password);
      message.success("登录成功");
      navigate("/chat", { replace: true });
    } catch (e) {
      message.error(e instanceof Error ? e.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex",
                  alignItems: "center", justifyContent: "center" }}>
      <Card style={{ width: 360 }}>
        <Typography.Title level={4} style={{ textAlign: "center" }}>
          CodeRAG 登录
        </Typography.Title>
        <Form layout="vertical" onFinish={onFinish}>
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
