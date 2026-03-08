import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    // --- 關鍵修改：允許外部裝置連線 ---
    host: true, 
    // 或是 host: '0.0.0.0'
    
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8000', // 建議改用 127.0.0.1 避開部分環境 localhost 解析慢的問題
        changeOrigin: true,
      },
      '/admin': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})