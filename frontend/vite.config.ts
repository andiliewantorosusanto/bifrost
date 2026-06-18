import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Dev server proxies into the running backend (Docker or native).
    proxy: {
      '/ws': { target: 'ws://127.0.0.1:7842', ws: true },
      '/library': 'http://127.0.0.1:7842',
    },
  },
})
