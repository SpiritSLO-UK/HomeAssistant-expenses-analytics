import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base so built assets load under any Home Assistant ingress path
// (e.g. /api/hassio_ingress/<token>/). See spec §26.3.
export default defineConfig({
  base: "./",
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
