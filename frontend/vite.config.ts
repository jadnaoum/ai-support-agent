import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// VITE_API_PROXY lets a dev server point at a backend on another port
// (e.g. while an eval run owns :8000).
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    proxy: { '/api': process.env.VITE_API_PROXY || 'http://localhost:8000' },
  },
  build: { outDir: 'dist' },
})
