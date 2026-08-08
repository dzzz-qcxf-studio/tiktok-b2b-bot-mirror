import { defineConfig, type UserConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

type VitestUserConfig = UserConfig & {
  test: {
    environment: string
    globals: boolean
  }
}

const config: VitestUserConfig = {
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
  },
}

export default defineConfig(config)
