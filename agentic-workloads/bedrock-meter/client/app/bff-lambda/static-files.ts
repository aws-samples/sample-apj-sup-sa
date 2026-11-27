import fs from "node:fs";
import path from "node:path";
import { SECURITY_HEADERS } from "./security-headers";

// Serves the Next static export bundled at <lambda>/public/. One Lambda Function URL
// serves both the UI and the BFF API → no public S3 (account has Block-Public-Access on)
// and no CloudFront. Static export emits /foo.html for route /foo, plus /_next/static/*.
const ROOT = path.join(__dirname, "public");

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".map": "application/json",
};
const TEXTUAL = new Set([".html", ".js", ".css", ".json", ".txt", ".svg", ".map"]);

type Result = { statusCode: number; headers: Record<string, string>; body: string; isBase64Encoded?: boolean };

// Map a request path to a file inside ROOT. "/" → index.html; "/users" → users.html
// (Next export). Prevents path traversal by resolving and confirming containment.
function resolveFile(rawPath: string): string | null {
  let rel = decodeURIComponent(rawPath.split("?")[0]).replace(/^\/+/, "");
  if (rel === "") rel = "index.html";

  const candidates: string[] = [];
  if (path.extname(rel)) {
    candidates.push(rel);
  } else {
    candidates.push(`${rel}.html`, path.join(rel, "index.html"));
  }

  for (const cand of candidates) {
    const abs = path.resolve(ROOT, cand);
    if (abs.startsWith(ROOT + path.sep) && fs.existsSync(abs) && fs.statSync(abs).isFile()) {
      return abs;
    }
  }
  return null;
}

export function serveStatic(rawPath: string): Result {
  const file = resolveFile(rawPath);
  const target = file ?? path.join(ROOT, "404.html");
  const found = Boolean(file);

  let ext = path.extname(target).toLowerCase();
  let abs = target;
  if (!fs.existsSync(abs)) {
    // No 404.html (shouldn't happen) → minimal text 404.
    return {
      statusCode: 404,
      headers: { "content-type": "text/plain", ...SECURITY_HEADERS },
      body: "Not found",
    };
  }

  const contentType = TYPES[ext] || "application/octet-stream";
  // Hashed Next assets are immutable; HTML should not be cached so deploys take effect.
  const cacheControl = rawPath.startsWith("/_next/static/")
    ? "public, max-age=31536000, immutable"
    : "no-cache";

  if (TEXTUAL.has(ext)) {
    return {
      statusCode: found ? 200 : 404,
      headers: { "content-type": contentType, "cache-control": cacheControl, ...SECURITY_HEADERS },
      body: fs.readFileSync(abs, "utf8"),
    };
  }
  return {
    statusCode: found ? 200 : 404,
    headers: { "content-type": contentType, "cache-control": cacheControl, ...SECURITY_HEADERS },
    body: fs.readFileSync(abs).toString("base64"),
    isBase64Encoded: true,
  };
}
