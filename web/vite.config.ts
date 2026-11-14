import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Inside Docker Compose the API is reachable as "api"; override locally with API_URL.
const apiUrl = process.env.API_URL ?? "http://api:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": apiUrl,
    },
  },
});
