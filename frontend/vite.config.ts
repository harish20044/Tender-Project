import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    // Required for the dev server to be reachable from outside the container.
    host: true,
    watch: {
      // Bind mounts on Windows and macOS do not deliver inotify events.
      usePolling: true,
    },
  },
});
