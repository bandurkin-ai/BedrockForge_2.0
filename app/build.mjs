// Bundles the main process and preload scripts (CommonJS, as Electron expects for sandboxed preload).
import { build } from "esbuild";

const common = { bundle: true, platform: "node", target: "node22", format: "cjs", external: ["electron"], logLevel: "warning" };
await build({ ...common, entryPoints: ["src/main/main.ts"], outfile: "dist/main.js" });
await build({ ...common, entryPoints: ["src/preload/preload.ts"], outfile: "dist/preload.js" });
await build({ ...common, entryPoints: ["src/preload/splash-preload.ts"], outfile: "dist/splash-preload.js" });
