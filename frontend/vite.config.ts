import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期把 /api 与 /ws 代理到后端（默认 127.0.0.1:8000），生产构建产物由后端托管
export default defineConfig({
  plugins: [react()],
  resolve: {
    // 必须与 tsconfig.json 的 paths 保持一致：类型检查靠 tsconfig，打包靠这里，
    // 只配一边会出现「tsc 通过但 vite build 找不到模块」的假绿。
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          // 高亮库体积较大，单独分块，避免拖慢首屏
          highlight: ['react-syntax-highlighter'],
          markdown: ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
})
