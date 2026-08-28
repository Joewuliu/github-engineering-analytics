/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local dev proxy targets the backend directly -- see README "Local frontend
// development". This keeps the browser on one origin (Vite's) in dev, exactly
// as production keeps everything on one origin (FastAPI's) -- no CORS either way.
const BACKEND_ORIGIN = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/auth": { target: BACKEND_ORIGIN, changeOrigin: true },
      "/me": { target: BACKEND_ORIGIN, changeOrigin: true },
      "/health": { target: BACKEND_ORIGIN, changeOrigin: true },
      "/ready": { target: BACKEND_ORIGIN, changeOrigin: true },
      "/repositories": { target: BACKEND_ORIGIN, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
    globals: true,
  },
});
