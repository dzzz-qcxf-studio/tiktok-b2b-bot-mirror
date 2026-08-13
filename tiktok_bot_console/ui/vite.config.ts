import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const apiBase = process.env.VITE_API_BASE || env.VITE_API_BASE || 'http://localhost:8000'

  return {
    plugins: [
      {
        name: 'tiktok-bot-runtime-marker',
        configureServer(server) {
          server.middlewares.use('/__tiktok-bot-runtime', (request, response, next) => {
            if (request.method !== 'GET') {
              next()
              return
            }
            response.statusCode = 200
            response.setHeader('Content-Type', 'application/json; charset=utf-8')
            response.setHeader('Cache-Control', 'no-store')
            response.end(JSON.stringify({
              appId: 'tiktok-b2b-bot-ui',
              apiBase,
            }))
          })
        },
      },
      vue(),
      ...(env.VITE_ENABLE_DEVTOOLS === 'true' ? [vueDevTools()] : []),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
  }
})
