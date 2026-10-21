// Vite build for the Saley dashboard v2 (R5 T10).
// D145: the build goes to src/saley/web/dist and is served under /static/app/.
// D146: the built index.html must hold no inline script. Only external module scripts.
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/app/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  build: {
    outDir: path.resolve(import.meta.dirname, "../src/saley/web/dist"),
    emptyOutDir: true,
    // The polyfill is an inline script; the CSP (script-src 'self') blocks it (D146).
    modulePreload: { polyfill: false },
    // Fonts stay files, never data: URIs, so font-src 'self' covers them.
    assetsInlineLimit: 0,
    sourcemap: false,
    target: "es2022",
  },
});
