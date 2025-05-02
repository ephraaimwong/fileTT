import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { Buffer } from "buffer";

// Polyfill Buffer globally
if (typeof window !== "undefined") {
  window.Buffer = Buffer;
}

export default defineConfig({
  plugins: [react()],
  define: {
    "process.env": {},
    global: "globalThis",
  },
  resolve: {
    alias: {
      buffer: "buffer",
      crypto: "crypto-browserify",
      process: "process/browser",
      stream: "stream-browserify",
      events: "events",
      util: "util",
    },
  },
  optimizeDeps: {
    include: [
      "buffer",
      "crypto-browserify",
      "process",
      "stream-browserify",
      "events",
      "util",
      "spake2",
    ],
  },
  build: {
    rollupOptions: {
      external: [], // Ensure no modules are externalized
    },
  },
});
