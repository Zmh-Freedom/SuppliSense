import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import checker from 'vite-plugin-checker'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendPort = env.SUPPLISENSE_BACKEND_PORT || '8000'
  // The dev backend binds to IPv4 loopback; using localhost may resolve to ::1
  // on macOS and make the Vite proxy return 502 despite a healthy backend.
  const backendUrl = `http://127.0.0.1:${backendPort}`

  return {
    plugins: [
      react(),
      tailwindcss(),
      checker({ typescript: true }),
    ],
    server: {
      host: '0.0.0.0',
      headers: {
        // 局域网演示始终获取当前入口与模块，避免客户端停留在旧 ChatView。
        'Cache-Control': 'no-store',
      },
      proxy: {
        '/api/v1': backendUrl,
        '/health': backendUrl,
        '/metrics': backendUrl,
        '/ws': { target: `ws://localhost:${backendPort}`, ws: true },
      },
    },
  }
})
