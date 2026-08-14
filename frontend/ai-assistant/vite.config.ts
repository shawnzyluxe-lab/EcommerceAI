import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  base: '/static/ai-assistant/',
  build: {
    outDir: path.resolve(__dirname, '../../static/ai-assistant'),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: ({ names }) => {
          const name = names?.[0] ?? 'asset'
          if (/\.css$/i.test(name)) return 'assets/[name][extname]'
          return 'assets/[name][extname]'
        },
      },
    },
  },
})
