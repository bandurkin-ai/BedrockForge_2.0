import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built files are served by the Electron shell under app://bedrockforge/.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist",
    target: "chrome130",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: { port: 5173, strictPort: true },
  test: { environment: "node", include: ["test/**/*.test.ts"] },
});
