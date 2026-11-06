import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// На Pages бренд-файли лежать поруч із застосунком: /domovyk/brand/ (pages.yml копіює
// assets/brand). У dev-сервері віддаємо їх тим самим шляхом, щоб картинки були й локально.
function brandAssets(): Plugin {
  const dir = resolve(__dirname, "../assets/brand");
  return {
    name: "domovyk-brand-assets",
    configureServer(server) {
      server.middlewares.use("/domovyk/brand", (req, res, next) => {
        const name = (req.url ?? "").split("?")[0].replace(/^\//, "");
        if (!/^[\w-]+\.svg$/.test(name)) return next();
        readFile(resolve(dir, name))
          .then((data) => {
            res.setHeader("Content-Type", "image/svg+xml");
            res.end(data);
          })
          .catch(() => next());
      });
    },
  };
}

// Mini App живе на GitHub Pages у /domovyk/app/ (ADR 0001).
export default defineConfig({
  base: process.env.VITE_BASE ?? "/domovyk/app/",
  plugins: [react(), brandAssets()],
  server: { port: 5173 },
});
