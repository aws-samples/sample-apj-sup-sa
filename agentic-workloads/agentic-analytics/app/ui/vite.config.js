import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Serve the app at /app as well as /app/. The Cognito callback URL registered by the
// stacks is <origin>/app, and Vite would otherwise answer 404 without the slash.
const serveBaseWithoutSlash = {
  name: 'serve-base-without-slash',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url === '/app' || req.url.startsWith('/app?')) req.url = '/app/' + req.url.slice(4);
      next();
    });
  },
};

// The app still reads process.env.REACT_APP_* (the Amplify and workshop config
// use those names), so expose exactly those keys at build time.
export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), 'REACT_APP_');
  // Dev server sits behind the Code Editor's nginx at /app; the hosted build is served from /.
  const base = command === 'serve' ? '/app/' : '/';
  const processEnv = {
    ...env,
    NODE_ENV: mode === 'production' ? 'production' : 'development',
    PUBLIC_URL: base.replace(/\/$/, ''),
  };
  return {
    base,
    plugins: [react(), serveBaseWithoutSlash],
    define: { 'process.env': JSON.stringify(processEnv) },
    envPrefix: 'REACT_APP_',
    // No dev websocket by default. Behind the Code Editor proxy it drops every so often, and
    // the Vite client reloads the page when it does, which wipes the chat (hmr: false alone
    // still opens the socket). UI_HMR=1 brings back hot reload for UI work.
    server: { host: '127.0.0.1', port: 3001, strictPort: true, allowedHosts: true,
              hmr: process.env.UI_HMR === '1', ...(process.env.UI_HMR === '1' ? {} : { ws: false }) },
    preview: { host: '127.0.0.1', port: 3001, strictPort: true },
    build: { outDir: 'build', sourcemap: false },
  };
});
