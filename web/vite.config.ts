import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    // 共享服务器的文件监听额度可能已满，开发模式使用轮询。
    watch: { usePolling: true, interval: 1000 },
    proxy: { '/api': { target: 'http://127.0.0.1:18501', changeOrigin: false } },
  },
})
