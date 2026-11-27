import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * Vite 构建与本地开发服务器配置。
 * 启用 Vue 单文件组件插件；开发服务器只监听本机回环地址，
 * 轮询监听源码变更，并把 /api 请求代理到本机后端服务。
 */
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    watch: { usePolling: true, interval: 1000 },
    proxy: { '/api': { target: 'http://127.0.0.1:18080', changeOrigin: false } },
  },
})
