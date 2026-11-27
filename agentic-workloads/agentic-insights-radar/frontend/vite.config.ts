// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 2500 },
  server: {
    port: 3000,
    // Local development proxies /api to a deployed stack: AIR_API_URL=https://<id>.execute-api... npm run dev
    proxy: process.env.AIR_API_URL ? { '/api': { target: process.env.AIR_API_URL, changeOrigin: true } } : undefined,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['src/test/setup.ts'],
    css: false,
    // Fixtures are written in UTC (audit rows at 10:00Z, "today" pinned by fake timers), so the suite pins
    // the zone: run on a laptop in Singapore it failed six audit tests that pass in CI. Code that depends on
    // the reader's zone is tested with dates built in local time, which holds in any zone.
    env: { TZ: 'UTC' },
    // One worker per file and a few hundred jsdom renders: on a busy machine a render that normally
    // takes 300 ms can take several seconds, and the 5 s default failed tests that had nothing wrong.
    testTimeout: 20_000,
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/main.tsx', 'src/vite-env.d.ts'],
      thresholds: { lines: 100, branches: 100, functions: 100, statements: 100 },
    },
  },
});
