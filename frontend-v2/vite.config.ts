import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 本地直连 vite 时用 proxy（/api 前缀 rewrite 剥离，同 v1；backend-v2 :8010）
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8010",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  // Monaco / worker 等后续优化
  optimizeDeps: {
    exclude: [],
  },
});
