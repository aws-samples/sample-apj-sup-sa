// Fails when any built file would not fit in one ALB -> Lambda response. dashboard.static() returns each file
// gzipped and base64-encoded, and the ALB refuses a Lambda response over 1 MB, so the limit is on the base64 length
// of the gzip with room left for headers and the JSON envelope.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';

const LIMIT = 960 * 1000; // bytes of base64(gzip(file))
const root = fileURLToPath(new URL('../../../lambda/dashboard_ui/', import.meta.url));

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

let files;
try {
  files = walk(root);
} catch {
  console.error(`No build output at ${root}. Run npm run build first.`);
  process.exit(1);
}
if (!files.length) {
  console.error('Build output is empty.');
  process.exit(1);
}

const kb = (n) => (n / 1024).toFixed(1).padStart(8) + ' KB';
let failed = false;
let total = 0;
let totalGz = 0;
for (const f of files.sort()) {
  const raw = readFileSync(f);
  const gzBytes = gzipSync(raw, { level: 9 });
  const gz = gzBytes.length;
  const b64 = Math.ceil(gz / 3) * 4;
  total += raw.length;
  totalGz += gz;
  const over = b64 > LIMIT;
  failed ||= over;
  console.log(`${over ? 'OVER' : 'ok  '} ${kb(raw.length)} raw ${kb(gz)} gz ${kb(b64)} b64  ${relative(root, f)}`);
}
console.log(`total ${kb(total)} raw ${kb(totalGz)} gz, ${files.length} files, limit ${LIMIT} bytes of base64 gzip per file`);
if (failed) {
  console.error('check-size: at least one file is over the per-response limit');
  process.exit(1);
}
