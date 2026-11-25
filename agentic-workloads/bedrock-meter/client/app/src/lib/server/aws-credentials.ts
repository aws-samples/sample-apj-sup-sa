import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export type AwsCredentials = {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken?: string;
};

function parseIni(content: string): Record<string, Record<string, string>> {
  const sections: Record<string, Record<string, string>> = {};
  let current: Record<string, string> | undefined;

  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || line.startsWith(";")) continue;

    const section = line.match(/^\[(.+)]$/);
    if (section) {
      current = {};
      sections[section[1].trim()] = current;
      continue;
    }

    const equalsIndex = line.indexOf("=");
    if (!current || equalsIndex < 0) continue;
    current[line.slice(0, equalsIndex).trim()] = line.slice(equalsIndex + 1).trim();
  }

  return sections;
}

function credentialsFromEnv(): AwsCredentials | undefined {
  // On Lambda these are the BFF execution ROLE's temporary creds. Core accepts that role as
  // an admin caller (ADMIN_ROLE_NAMES), so we sign with them directly — including
  // AWS_SESSION_TOKEN, which sigv4 emits as x-amz-security-token (required for temporary
  // creds). Falls back to the same AWS_* vars for local dev with an IAM user.
  const accessKeyId = process.env.AWS_ACCESS_KEY_ID;
  const secretAccessKey = process.env.AWS_SECRET_ACCESS_KEY;
  if (!accessKeyId || !secretAccessKey) return undefined;

  return {
    accessKeyId,
    secretAccessKey,
    sessionToken: process.env.AWS_SESSION_TOKEN,
  };
}

function credentialsFromSharedFile(): AwsCredentials | undefined {
  const profile = process.env.AWS_PROFILE || "default";
  const filePath =
    process.env.AWS_SHARED_CREDENTIALS_FILE || path.join(os.homedir(), ".aws", "credentials");

  if (!fs.existsSync(filePath)) return undefined;

  const section = parseIni(fs.readFileSync(filePath, "utf8"))[profile];
  if (!section?.aws_access_key_id || !section?.aws_secret_access_key) return undefined;

  return {
    accessKeyId: section.aws_access_key_id,
    secretAccessKey: section.aws_secret_access_key,
    sessionToken: section.aws_session_token,
  };
}

export function resolveAwsCredentials(): AwsCredentials {
  const credentials = credentialsFromEnv() || credentialsFromSharedFile();
  if (credentials) return credentials;

  throw new Error(
    "AWS credentials were not found. Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or use an AWS_PROFILE with static credentials.",
  );
}
