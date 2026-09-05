/** V2-M9 登录客户端。 */
import { api } from "./client";

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: {
    username: string;
    role: string;
    allowed_scopes: Record<string, unknown>;
    endpoint_classes: string[];
  };
}

export const login = (body: { username: string; password: string }) =>
  api.post<LoginResponse>("/v1/auth/login", body).then((r) => r.data);

export interface MePayload {
  username: string;
  role: string;
  allowed_scopes: Record<string, unknown>;
  endpoint_classes: string[];
}

export const fetchMe = () =>
  api.get<{ user: MePayload }>("/v1/auth/me").then((r) => r.data.user);

export const logout = () => api.post("/v1/auth/logout").then((r) => r.data);
