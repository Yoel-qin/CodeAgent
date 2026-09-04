import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 本地直连 vite 时用 proxy（/api 前缀 rewrite 剥离，同 v1；backend-v2 :8010）。
// 端口 5300：5173/5174 落在 Windows Hyper-V/WinNAT 动态保留段（如 5130-5229，随重启
// 随机重排，netsh interface ipv4 show excludedportrange protocol=tcp 可查）→ bind 即
// EACCES，strictPort 下直接退出；5300 避开该段。
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5300,
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
