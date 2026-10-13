import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    rollupOptions: {
      external: [
        'electron',
        'better-sqlite3',
        'ws',
        '@modelcontextprotocol/sdk',
        '@modelcontextprotocol/sdk/client',
        '@modelcontextprotocol/sdk/client/stdio.js',
      ],
      output: {
        format: 'cjs',
      },
    },
  },
  resolve: {
    alias: {
      '@': '/src',
      '@shared': '/src/shared',
    },
  },
});
