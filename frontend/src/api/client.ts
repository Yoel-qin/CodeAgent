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

// 请求拦截器：自动附加 Authorization header
api.interceptors.request.use((cfg) => {
  const t = getToken();
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});

// 401 响应拦截器：清除 token 并跳转登录页（login 请求自身与登录页不重定向）
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err.response?.status ?? 0;
    const url: string = err.config?.url ?? "";
    if (status === 401 && !url.includes("/auth/login") && window.location.pathname !== "/login") {
      clearToken();
      window.location.assign("/login");
    }
    return Promise.reject(err);
  },
);

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

// ---- 健康检查 ----
export interface HealthResponse {
  status: string;
  app: string;
  env: string;
  components: Record<string, boolean>;
  auth_required?: boolean;
}

export const getHealth = () => api.get<HealthResponse>("/health").then((r) => r.data);

// ---- SSE 工具（Phase 1 问答用） ----
export { streamChat } from "./sse";
