/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
  server: {
    port: 5173,
    proxy: {
      // Route /api/* to the backend app API during local dev (mirrors the
      // CloudFront /api/* behavior in production).
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
