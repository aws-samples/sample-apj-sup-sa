import path from "node:path";
import { fileURLToPath } from "node:url";

const appDir = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export bundled into the single BFF Lambda (bff-lambda/dist/public) and served behind
  // a public HTTP API ($default) alongside /api/console/* — no S3 hosting, no Function URL.
  // (account guardrails block public S3 + public Function URLs.) NEXT_PUBLIC_CONSOLE_API_BASE is
  // a dev-only override; in prod the UI and API share one origin.
  output: "export",
  outputFileTracingRoot: appDir,
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
