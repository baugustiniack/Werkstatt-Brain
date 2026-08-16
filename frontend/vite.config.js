import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

// Werkstatt-Brain Dashboard (SPEC Kap. 5) – Vite-Konfiguration.
// Hinweis: Liegt js und ts parallel, hat js Vorrang – Proxy hier pflegen.
//
// Windows+Docker: aggressives usePolling ohne Interval kann CPU/Disk so belasten,
// dass der Host einfriert. Polling nur auf Anfrage (CHOKIDAR_USEPOLLING=true),
// sonst natives Watching; immer schwere Pfade ignorieren.
const usePolling = ["1", "true", "yes"].includes(
  String(process.env.CHOKIDAR_USEPOLLING || process.env.VITE_USE_POLLING || "").toLowerCase(),
);
const pollInterval = Number(process.env.CHOKIDAR_INTERVAL || process.env.VITE_POLL_INTERVAL || 3000);

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
      usePolling,
      ...(usePolling ? { interval: Number.isFinite(pollInterval) ? pollInterval : 3000 } : {}),
      ignored: ["**/node_modules/**", "**/.git/**", "**/dist/**", "**/*.log", "**/package-lock.json"],
    },
  },
});
