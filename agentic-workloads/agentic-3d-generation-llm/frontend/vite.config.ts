import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  // three.js (inside model-viewer) is most of the bundle.
  build: { chunkSizeWarningLimit: 1500 },
})
