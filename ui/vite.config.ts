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
    // three.js is one lazy chunk shared by the 3D window and its layers (ADR 0004 §1: never in the main bundle)
    rollupOptions: { output: { manualChunks: (id) => (id.includes("/node_modules/three/") ? "three" : undefined) } },
  },
  server: { port: 5173, strictPort: true },
  test: { environment: "node", include: ["test/**/*.test.ts"] },
});
