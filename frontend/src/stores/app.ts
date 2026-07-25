import { create } from "zustand";
import { getHealth, type HealthResponse } from "../api/client";

interface AppState {
  health: HealthResponse | null;
  healthLoading: boolean;
  fetchHealth: () => Promise<void>;
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
}));
