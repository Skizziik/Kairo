import { defineConfig } from "vite";

export default defineConfig({
  server: { port: 5174, host: true, cors: true, allowedHosts: true },
  build: { target: "es2020", sourcemap: true },
});
