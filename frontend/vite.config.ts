import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import checker from 'vite-plugin-checker'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendPort = env.SUPPLISENSE_BACKEND_PORT || '8000'
  const backendUrl = `http://localhost:${backendPort}`

  return {
    plugins: [
      react(),
      tailwindcss(),
      checker({ typescript: true }),
    ],
    server: {
      proxy: {
        '/api/v1': backendUrl,
        '/health': backendUrl,
        '/metrics': backendUrl,
        '/ws': { target: `ws://localhost:${backendPort}`, ws: true },
      },
    },
  }
})
