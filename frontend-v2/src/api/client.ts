import axios from "axios";

const TOKEN_KEY = "coderag_token";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

export const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
});

// 请求侧：带 JWT（M9 RBAC；SSE 侧 sse.ts 已单独带）
api.interceptors.request.use((config) => {
  const t = getToken();
  if (t) config.headers.Authorization = `Bearer ${t}`;
  return config;
});

// 统一错误格式（对齐 api 接口清单：{ error_code, message, status }）；
// 401（token 过期/被禁）→ 清 token 回登录页；未登录首访由 Workbench 守卫处理
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401 && getToken()) {
      clearToken();
      localStorage.removeItem("coderag_username");
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
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
  auth_required?: boolean;
  components: Record<string, { status?: string; error?: string } & Record<string, unknown>>;
}

export const getHealth = () => api.get<HealthResponse>("/health").then((r) => r.data);

// ---- SSE 工具 ----
export { streamChat } from "./sse";
