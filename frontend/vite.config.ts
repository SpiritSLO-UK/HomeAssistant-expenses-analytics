import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import pkg from "./package.json";

// Relative base so built assets load under any Home Assistant ingress path
// (e.g. /api/hassio_ingress/<token>/). See spec §26.3.
export default defineConfig({
  base: "./",
  // Expose the package version to the app so the sidebar badge can't drift from
  // the real release version (which is bumped in package.json at release).
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // Forward API calls to the FastAPI backend during local dev.
      "/api": "http://localhost:8099",
    },
  },
});
