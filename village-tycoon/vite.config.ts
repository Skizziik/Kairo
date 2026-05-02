import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    host: true,
    // Allow Cloudflare Tunnel host headers + dev tools
    cors: true,
    allowedHosts: true,
  },
  build: {
    target: "es2020",
    sourcemap: true,
  },
});
