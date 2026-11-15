import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Inside Docker Compose the API is reachable as "api"; override locally with API_URL.
const apiUrl = process.env.API_URL ?? "http://api:8000";

export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 600 }, // the lazily loaded 3D chunk (three.js) is about 520 kB
  server: {
    port: 5173,
    // In Docker on Windows the bind-mounted sources send no file events, so after `git pull` the dev
    // server would keep serving the old interface. Polling (VITE_POLL=1 in docker-compose) notices the changes.
    watch: process.env.VITE_POLL ? { usePolling: true, interval: 1000 } : undefined,
    proxy: {
      "/api": apiUrl,
    },
  },
});
