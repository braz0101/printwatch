import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Le build est écrit dans le paquet Python : FastAPI sert ensuite des fichiers statiques (pas de Node en production).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: '../printwatch/static', emptyOutDir: true, chunkSizeWarningLimit: 700 },
  server: { proxy: { '/api': 'http://127.0.0.1:8080' } }, // `npm run dev` + `printwatch serve` en parallèle
})
