import { api } from "./client";

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: { username: string; role: string };
}

export const login = (username: string, password: string) =>
  api.post<LoginResponse>("/v1/auth/login", { username, password }).then((r) => r.data);
