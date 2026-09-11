import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Resolved from import.meta.url rather than __dirname, which does not
    // exist in an ES module and only works here by Vite's bundling.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    // Not Vite's 5173 default — shifted so this project can run alongside
    // another one on the same machine without a port collision.
    port: 5174,
    // Required for the dev server to be reachable from outside the container.
    host: true,
    // Same-origin in development, so no CORS negotiation and no API base URL
    // to configure before the dashboard will load.
    //
    // The target differs by where this runs. On the host the API is on
    // localhost; inside Compose it is another container, and localhost would
    // resolve to this container itself. Compose sets VITE_PROXY_TARGET.
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
    watch: {
      // Bind mounts on Windows and macOS do not deliver inotify events.
      usePolling: true,
    },
  },
});
