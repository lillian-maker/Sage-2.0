import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const target = process.env.SAGE_API_TARGET || 'http://127.0.0.1:8000';
const api = { '/api': { target, changeOrigin: false } };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: api },
  preview: { port: 4173, strictPort: true, proxy: api },
  build: { outDir: 'dist', sourcemap: true },
});
