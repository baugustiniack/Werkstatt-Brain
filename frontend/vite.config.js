import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";
// Werkstatt-Brain Dashboard (SPEC Kap. 5) – Vite-Konfiguration.
export default defineConfig({
    plugins: [
        react(),
        tailwindcss(),
        VitePWA({
            registerType: "autoUpdate",
            includeAssets: ["icons/*.svg"],
            // SPEC Kap. 5.4 nennt explizit `public/manifest.json` als Manifest-Datei.
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
                // Statische Assets fürs schnelle Laden in der Werkstatt cachen (Kap. 5.4);
                // API-/WS-Aufrufe bewusst NICHT cachen (Live-Agenten-Daten dürfen nie stale sein).
                globPatterns: ["**/*.{js,css,html,svg,png,ico}"],
            },
        }),
    ],
    server: {
        host: true,
        port: 5173,
        strictPort: true,
        watch: {
            // Docker-Bind-Mounts auf Windows-Hosts liefern keine zuverlässigen
            // inotify-Events – ohne Polling bemerkt Vite Dateiänderungen nicht.
            usePolling: true,
        },
    },
});
