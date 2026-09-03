import axios from "axios";

const TOKEN_KEY = "coderag_token";

// v2 暂无 RBAC；getToken 仍被 sse.ts 引用（M9 RBAC 回接时恢复请求/401 拦截器）
export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

export const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
});

// 统一错误格式（对齐 api 接口清单：{ error_code, message, status }）
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const data = err.response?.data;
    return Promise.reject(
      Object.assign(new Error(data?.message || err.message || "请求失败"), {
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
  components: Record<string, { status?: string; error?: string } & Record<string, unknown>>;
}

export const getHealth = () => api.get<HealthResponse>("/health").then((r) => r.data);

// ---- SSE 工具 ----
export { streamChat } from "./sse";
