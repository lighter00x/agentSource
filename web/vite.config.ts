import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy:
        env.WORKBENCH_MOCK_API === "1"
          ? undefined
          : {
              "/api": {
                target: env.DRUGPAPER_API_TARGET || "http://127.0.0.1:18000",
                changeOrigin: true,
              },
              "/docs": {
                target: env.DRUGPAPER_API_TARGET || "http://127.0.0.1:18000",
                changeOrigin: true,
              },
              "/openapi.json": {
                target: env.DRUGPAPER_API_TARGET || "http://127.0.0.1:18000",
                changeOrigin: true,
              },
              "/healthz": {
                target: env.DRUGPAPER_API_TARGET || "http://127.0.0.1:18000",
                changeOrigin: true,
              },
            },
    },
  };
});
