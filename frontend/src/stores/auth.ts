import { create } from "zustand";
import { clearToken, getToken, setToken } from "../api/client";
import { login as apiLogin } from "../api/auth";

interface AuthState {
  token: string | null;
  username: string | null;
  role: string | null;
  hydrate: () => void;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
}

const USER_KEY = "coderag_user";

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  username: null,
  role: null,
  hydrate: () => {
    const token = getToken();
    let username: string | null = null;
    let role: string | null = null;
    try {
      const raw = localStorage.getItem(USER_KEY);
      if (raw) ({ username, role } = JSON.parse(raw));
    } catch { /* 坏 JSON 忽略 */ }
    set({ token, username, role });
  },
  login: async (u, p) => {
    const res = await apiLogin(u, p);
    setToken(res.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(res.user));
    set({ token: res.access_token, username: res.user.username, role: res.user.role });
  },
  logout: () => {
    clearToken();
    localStorage.removeItem(USER_KEY);
    set({ token: null, username: null, role: null });
  },
}));
