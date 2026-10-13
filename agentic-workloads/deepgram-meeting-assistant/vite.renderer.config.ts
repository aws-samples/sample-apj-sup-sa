import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

interface ForgeAwareEnv {
  command: 'build' | 'serve';
  mode: string;
  forgeConfigSelf?: {
    name?: string;
  };
}

export default defineConfig((env: ForgeAwareEnv) => {
  const isForgeBuild = Boolean(env.forgeConfigSelf);
  const rendererRoot = path.resolve(__dirname, 'src/renderer');
  const forgeRendererName = env.forgeConfigSelf?.name ?? 'main_window';
  const forgeRendererOutDir = path.resolve(__dirname, '.vite/renderer', forgeRendererName);

  return {
    root: rendererRoot,
    publicDir: path.resolve(rendererRoot, 'public'),
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src'),
        '@shared': path.resolve(__dirname, 'src/shared'),
      },
    },
    worker: {
      format: 'es',
      rollupOptions: {
        output: {
          entryFileNames: '[name].js',
          chunkFileNames: '[name].js',
        },
      },
    },
    build: {
      outDir: isForgeBuild ? forgeRendererOutDir : undefined,
      rollupOptions: {
        input: path.resolve(rendererRoot, 'index.html'),
        output: {
          entryFileNames: '[name].js',
          chunkFileNames: '[name].js',
        },
      },
    },
  };
});
