import axios from "axios";

export const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
});

// 统一错误格式（对齐 api 接口清单：{ error_code, message, status }）
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err.response?.status ?? 0;
    const data = err.response?.data;
    return Promise.reject(
      Object.assign(new Error(data?.message || err.message || "请求失败"), {
        status,
        error_code: data?.error_code,
        raw: data,
      }),
    );
  },
);

// ---- 健康检查 ----
export interface HealthResponse {
  status: string;
  app: string;
  env: string;
  components: Record<string, boolean>;
}

export const getHealth = () => api.get<HealthResponse>("/health").then((r) => r.data);

// ---- SSE 工具（Phase 1 问答用） ----
export { streamChat } from "./sse";
