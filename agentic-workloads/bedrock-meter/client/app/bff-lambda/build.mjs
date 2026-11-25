// Bundles the BFF Lambda handler (and its @/lib/server/* deps) into a single CJS file
// for the Node 24 Lambda runtime. Resolves the "@/..." tsconfig path alias to ./src.
import { build } from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
// .cjs (not .js): client/app package.json has "type":"module", so a .js CJS bundle
// would be misread as ESM and its module.exports ignored. .cjs forces CommonJS.
const outfile = path.join(appDir, "bff-lambda", "dist", "index.cjs");

await build({
  entryPoints: [path.join(appDir, "bff-lambda", "handler.ts")],
  bundle: true,
  platform: "node",
  target: "node24",
  format: "cjs",
  outfile,
  // Lambda function entry expects `exports.handler`.
  banner: { js: "" },
  alias: { "@": path.join(appDir, "src") },
  // The Node 24 Lambda runtime bundles AWS SDK v3 — don't ship @aws-sdk/* in the artifact.
  external: ["@aws-sdk/*"],
  logLevel: "info",
});

console.log(`BFF lambda bundled → ${outfile}`);
