import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

// Renderer (React) は frontend/ をルートとしてビルドする。
// Electron から file:// で読み込むため base は './'。
export default defineConfig({
  root: resolve(__dirname, 'frontend'),
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true
  },
  build: {
    outDir: resolve(__dirname, 'frontend/dist'),
    emptyOutDir: true
  }
});
