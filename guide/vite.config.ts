import path from "node:path"
import { fileURLToPath } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const rootDir = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  // Served at https://<RADAR_WEB_DOMAIN>/guide/ behind Caddy.
  base: "/guide/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(rootDir, "./src"),
    },
  },
  optimizeDeps: {
    exclude: ["takumi-pdf"],
  },
  build: {
    chunkSizeWarningLimit: 800,
  },
})
