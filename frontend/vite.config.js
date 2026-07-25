import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// 容器内由 nginx 反代 /api -> backend；本地直连 vite 时用 proxy
export default defineConfig({
    plugins: [react()],
    server: {
        host: true,
        port: 5173,
        strictPort: true,
        proxy: {
            "/api": {
                target: "http://localhost:8000",
                changeOrigin: true,
                rewrite: function (p) { return p.replace(/^\/api/, ""); },
            },
            "/ws": {
                target: "ws://localhost:8000",
                ws: true,
            },
        },
    },
    // Monaco / worker 等后续优化
    optimizeDeps: {
        exclude: [],
    },
});
