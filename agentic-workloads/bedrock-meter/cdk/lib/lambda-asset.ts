import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

const LAMBDA_ROOT = path.join(__dirname, '../../lambda');
const COMMON_DIR = path.join(LAMBDA_ROOT, 'common');

function copyDir(src: string, dst: string): void {
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name === '.DS_Store') continue;
    const s = path.join(src, entry.name);
    const d = path.join(dst, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

/**
 * Build a Lambda Code asset for `lambda/<handlerName>/` with `lambda/common/` bundled in.
 *
 * CDK hashes the staging dir, so any change to either the handler dir or common dir
 * invalidates the asset hash and triggers a redeploy automatically.
 */
export function lambdaCode(handlerName: string): lambda.Code {
  const handlerDir = path.join(LAMBDA_ROOT, handlerName);
  if (!fs.existsSync(handlerDir)) {
    throw new Error(`Lambda dir not found: ${handlerDir}`);
  }
  const stage = fs.mkdtempSync(path.join(os.tmpdir(), `lambda-${handlerName}-`));
  copyDir(handlerDir, stage);
  copyDir(COMMON_DIR, path.join(stage, 'common'));
  return lambda.Code.fromAsset(stage, {
    assetHash: hashTree(stage),
  });
}

function hashTree(dir: string): string {
  const h = crypto.createHash('sha256');
  function walk(p: string) {
    const stat = fs.statSync(p);
    if (stat.isDirectory()) {
      for (const e of fs.readdirSync(p).sort()) walk(path.join(p, e));
    } else {
      h.update(p.replace(dir, ''));
      h.update(fs.readFileSync(p));
    }
  }
  walk(dir);
  return h.digest('hex');
}
