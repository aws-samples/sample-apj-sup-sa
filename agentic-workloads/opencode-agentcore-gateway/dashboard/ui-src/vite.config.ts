// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Output goes straight into the Lambda package, which serves / and /assets/*.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: '/',
  // Mock mode is a dev-server feature only: the production build always compiles it out.
  define: command === 'build' ? { 'import.meta.env.VITE_MOCK': JSON.stringify('0') } : {},
  build: {
    outDir: '../../lambda/dashboard_ui',
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
    chunkSizeWarningLimit: 2500,
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (!id.includes('node_modules')) return undefined;
          if (/[\\/](react|react-dom|scheduler|react-router|react-router-dom|@remix-run)[\\/]/.test(id)) return 'react';
          return undefined;
        },
      },
    },
  },
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
}));
