import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/auth': 'http://localhost:8000',
      '/chat': 'http://localhost:8000',
      '/alert': 'http://localhost:8000',
      '/company': 'http://localhost:8000',
      '/risk': 'http://localhost:8000',
      '/financial': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/sentiment': 'http://localhost:8000',
      '/p2': 'http://localhost:8000',
      '/analysis': 'http://localhost:8000',
      '/knowledge': 'http://localhost:8000',
    },
  },
})
