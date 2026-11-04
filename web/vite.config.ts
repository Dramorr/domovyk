import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Mini App живе на GitHub Pages у /domovyk/app/ (ADR 0001).
export default defineConfig({
  base: process.env.VITE_BASE ?? "/domovyk/app/",
  plugins: [react()],
  server: { port: 5173 },
});
