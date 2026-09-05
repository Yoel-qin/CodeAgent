import { create } from "zustand";
import { getHealth, type HealthResponse } from "../api/client";
import { fetchMe, type MePayload } from "../api/auth";
import type { Citation } from "../hooks/types";

/** 当前聚焦的引用（由聊天引用卡片点击设置，供右侧上下文面板读取）；repo 用于 /v1/code/read、/v1/docs/section。 */
export type FocusedCitation = { repo: string } & Citation;

interface AppState {
  health: HealthResponse | null;
  healthLoading: boolean;
  fetchHealth: () => Promise<void>;
  focused: FocusedCitation | null;
  setFocused: (c: FocusedCitation | null) => void;
  cmdkOpen: boolean;
  setCmdkOpen: (b: boolean) => void;
  me: MePayload | null;
  meResolved: boolean; // /me 探测完成（401/失败也算）——守卫区分「探测中」与「未登录」
  fetchMe: () => Promise<void>;
  clearMe: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  health: null,
  healthLoading: false,
  fetchHealth: async () => {
    set({ healthLoading: true });
    try {
      const h = await getHealth();
      set({ health: h });
    } catch {
      set({ health: null });
    } finally {
      set({ healthLoading: false });
    }
  },
  focused: null,
  setFocused: (c) => set({ focused: c }),
  cmdkOpen: false,
  setCmdkOpen: (b) => set({ cmdkOpen: b }),
  me: null,
  meResolved: false,
  fetchMe: async () => {
    try {
      set({ me: await fetchMe(), meResolved: true });
    } catch {
      set({ me: null, meResolved: true }); // 401 已由拦截器跳登录；此处只落状态
    }
  },
  clearMe: () => set({ me: null, meResolved: false }),
}));
