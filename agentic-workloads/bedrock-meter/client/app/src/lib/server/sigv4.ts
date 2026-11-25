import crypto from "node:crypto";
import type { AwsCredentials } from "@/lib/server/aws-credentials";

type SignRequestOptions = {
  method: string;
  url: URL;
  region: string;
  service: string;
  body?: string;
  credentials: AwsCredentials;
  extraHeaders?: Record<string, string>;
};

function hash(value: string): string {
  return crypto.createHash("sha256").update(value, "utf8").digest("hex");
}

function hmac(key: Buffer | string, value: string): Buffer {
  return crypto.createHmac("sha256", key).update(value, "utf8").digest();
}

function encodeRfc3986(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, (char) =>
    `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

function canonicalPath(pathname: string): string {
  return pathname
    .split("/")
    .map((segment) => encodeRfc3986(decodeURIComponent(segment)))
    .join("/");
}

function byteCompare(left: string, right: string): number {
  // SigV4 requires byte-order sort. localeCompare is locale-dependent (e.g. ja_JP
  // collation differs from en_US) and would produce signatures the API Gateway
  // rejects. Comparing raw codepoints matches what requests-aws4auth/the SigV4
  // spec expects.
  return left < right ? -1 : left > right ? 1 : 0;
}

function canonicalQuery(url: URL): string {
  return Array.from(url.searchParams.entries())
    .sort(([leftKey, leftValue], [rightKey, rightValue]) =>
      leftKey === rightKey ? byteCompare(leftValue, rightValue) : byteCompare(leftKey, rightKey),
    )
    .map(([key, value]) => `${encodeRfc3986(key)}=${encodeRfc3986(value)}`)
    .join("&");
}

function amzTimestamp(now: Date): { dateStamp: string; requestTimestamp: string } {
  const iso = now.toISOString().replace(/[:-]|\.\d{3}/g, "");
  return {
    dateStamp: iso.slice(0, 8),
    requestTimestamp: iso,
  };
}

export function signAwsRequest({
  method,
  url,
  region,
  service,
  body = "",
  credentials,
  extraHeaders = {},
}: SignRequestOptions): Record<string, string> {
  const { dateStamp, requestTimestamp } = amzTimestamp(new Date());
  const payloadHash = hash(body);
  const headers: Record<string, string> = {
    host: url.host,
    "x-amz-content-sha256": payloadHash,
    "x-amz-date": requestTimestamp,
    ...Object.fromEntries(Object.entries(extraHeaders).map(([key, value]) => [key.toLowerCase(), value])),
  };

  if (credentials.sessionToken) {
    headers["x-amz-security-token"] = credentials.sessionToken;
  }

  const sortedHeaderNames = Object.keys(headers).sort();
  const canonicalHeaders = sortedHeaderNames.map((key) => `${key}:${headers[key].trim()}\n`).join("");
  const signedHeaders = sortedHeaderNames.join(";");
  const credentialScope = `${dateStamp}/${region}/${service}/aws4_request`;
  const canonicalRequest = [
    method.toUpperCase(),
    canonicalPath(url.pathname),
    canonicalQuery(url),
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");
  const stringToSign = [
    "AWS4-HMAC-SHA256",
    requestTimestamp,
    credentialScope,
    hash(canonicalRequest),
  ].join("\n");
  const signingKey = hmac(
    hmac(hmac(hmac(`AWS4${credentials.secretAccessKey}`, dateStamp), region), service),
    "aws4_request",
  );
  const signature = crypto.createHmac("sha256", signingKey).update(stringToSign, "utf8").digest("hex");

  return {
    ...headers,
    authorization:
      `AWS4-HMAC-SHA256 Credential=${credentials.accessKeyId}/${credentialScope}, ` +
      `SignedHeaders=${signedHeaders}, Signature=${signature}`,
  };
}
