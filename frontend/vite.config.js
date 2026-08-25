import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // 前端默认用相对路径（App.jsx/ScenarioForm 的 API_BASE=""）。
    // 本地 dev 时代理 /api → 后端 8000，保持前后端跨端口开发可跑；
    // 生产由 nginx 反代 /api → backend:8000（见根目录 nginx.conf）。
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
