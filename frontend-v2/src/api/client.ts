import axios from "axios";

export const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
});

// 统一错误格式（对齐 api 接口清单：{ error_code, message, status }）；
// 401 只在 RBAC on 时出现（cookie 过期/无效），httpOnly token 前端读不到——一律回登录页
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401 && !window.location.pathname.startsWith("/login")) {
      localStorage.removeItem("coderag_username");
      window.location.href = "/login";
    }
    const data = err.response?.data;
    return Promise.reject(
      Object.assign(new Error((data?.detail ?? data?.message) || err.message || "请求失败"), {
        status: err.response?.status ?? 0,
        error_code: data?.error_code,
        raw: data,
      }),
    );
  },
);

// ---- 健康检查（v2 形状：{status, components}） ----
export interface HealthResponse {
  status: string;
  auth_required?: boolean;
  components: Record<string, { status?: string; error?: string } & Record<string, unknown>>;
}

export const getHealth = () => api.get<HealthResponse>("/health").then((r) => r.data);

// ---- SSE 工具 ----
export { streamChat } from "./sse";
