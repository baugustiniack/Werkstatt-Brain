import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

// Werkstatt-Brain Dashboard (SPEC Kap. 5) – Vite-Konfiguration.
// Hinweis: Liegt js und ts parallel, hat js Vorrang – Proxy in beiden pflegen.
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icons/*.svg"],
      manifestFilename: "manifest.json",
      manifest: {
        name: "Werkstatt-Brain",
        short_name: "Werkstatt-Brain",
        description: "Autonomes Werkstatt-Brain & CAD/CAM-Agenten-Dashboard",
        theme_color: "#1e1e2e",
        background_color: "#1e1e2e",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "icons/icon.svg", sizes: "192x192", type: "image/svg+xml" },
          { src: "icons/icon.svg", sizes: "512x512", type: "image/svg+xml", purpose: "maskable" },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,ico}"],
      },
    }),
  ],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET || "http://api:8000",
        changeOrigin: true,
        ws: true,
      },
      "/health": {
        target: process.env.VITE_PROXY_TARGET || "http://api:8000",
        changeOrigin: true,
      },
    },
    watch: {
      usePolling: true,
    },
  },
});
