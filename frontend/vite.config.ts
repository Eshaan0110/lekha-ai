/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser only ever talks to the Vite server; /api and /health are proxied to the
// local API, so the API needs no CORS opening. Bound to 127.0.0.1: not reachable from the
// network.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
