import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // API 代理目标可用环境变量 VITE_API_TARGET 覆盖（如指向非默认后端端口）
  const apiTarget = loadEnv(mode, process.cwd(), '').VITE_API_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: {
      port: 3000,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
