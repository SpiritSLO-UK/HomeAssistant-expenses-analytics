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
    rollupOptions: {
      output: {
        // Split large, rarely-changing vendor code out of the app chunk so the
        // initial download shrinks and browsers can reuse the vendor bundles
        // across app releases (they only change when the deps do). This clears
        // Vite's ">500 kB chunk after minification" build warning. Regex matches
        // both POSIX and Windows path separators inside node_modules.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          // React core + router share the same release cadence: keep them together.
          if (
            /[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|@remix-run[\\/]router|scheduler)[\\/]/.test(
              id,
            )
          ) {
            return "react-vendor";
          }
          // TanStack Query is a sizeable, independent vendor: give it its own chunk.
          if (id.includes("@tanstack")) return "query-vendor";
          return "vendor";
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Forward API calls to the FastAPI backend during local dev.
      "/api": "http://localhost:8099",
    },
  },
});
