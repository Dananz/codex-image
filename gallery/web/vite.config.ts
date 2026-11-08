import { defineConfig, type PluginOption } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const API = 'http://127.0.0.1:8765'

export default defineConfig(async () => {
  const plugins: PluginOption[] = [react(), tailwindcss()]
  if (process.env.GALLERY_MOCK) plugins.push((await import('./dev/mock')).mockApi())
  return {
    plugins,
    build: { outDir: '../dist', emptyOutDir: true, chunkSizeWarningLimit: 800 },
    server: process.env.GALLERY_MOCK ? {} : {
      proxy: Object.fromEntries(['/api', '/thumb', '/file', '/ref'].map(p => [p, { target: API, changeOrigin: false }])),
    },
  }
})
