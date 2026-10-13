import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// SPA build → dist/ → S3 → CloudFront (see backend/infra WebStack).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    port: 5173,
  },
});
