import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    watch: { usePolling: true, interval: 1000 },
    proxy: { '/api': { target: 'http://127.0.0.1:18080', changeOrigin: false } },
  },
})
