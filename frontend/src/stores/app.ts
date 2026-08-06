import { create } from "zustand";
import { getHealth, type HealthResponse } from "../api/client";

/** 当前聚焦的引用 chunk（由聊天引用卡片点击设置，供右侧上下文面板读取）。 */
export interface FocusedCitation {
  chunk_id: string;
  type: "code" | "doc";
  label: string;
}

interface AppState {
  health: HealthResponse | null;
  healthLoading: boolean;
  fetchHealth: () => Promise<void>;
  focused: FocusedCitation | null;
  setFocused: (c: FocusedCitation | null) => void;
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
}));
