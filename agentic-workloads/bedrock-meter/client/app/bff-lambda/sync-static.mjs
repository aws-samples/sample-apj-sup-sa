// Copies the Next static export (out/) into the Lambda bundle (bff-lambda/dist/public/), so
// the single BFF Lambda serves both the UI and the API. Runs after `next build` and the BFF
// esbuild bundle. Pure Node — no shell dependency — so it works in CI and the deploy script.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const outDir = path.join(appDir, "out");
const publicDir = path.join(appDir, "bff-lambda", "dist", "public");

if (!fs.existsSync(outDir)) {
  console.error(`Static export not found at ${outDir}. Run \`next build\` first.`);
  process.exit(1);
}

fs.rmSync(publicDir, { recursive: true, force: true });
fs.mkdirSync(publicDir, { recursive: true });
fs.cpSync(outDir, publicDir, { recursive: true });

console.log(`Static UI synced → ${publicDir}`);
