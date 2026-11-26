// Origin-request gate for /api/*: verifies the httpOnly Cognito ID token cookie set by the
// viewer gate, then forwards it as the Authorization header that API Gateway's Cognito
// authorizer checks again. Requests without a valid token get 401 and never reach the origin.
import type { CloudFrontRequest, CloudFrontRequestEvent, CloudFrontRequestResult } from 'aws-lambda';
import { CognitoJwtVerifier } from 'aws-jwt-verify';
import { loadConfig, memo } from './config';

const getVerifier = memo(async () => {
  const config = await loadConfig();
  const verifier = CognitoJwtVerifier.create({ userPoolId: config.userPoolId, clientId: config.clientId, tokenUse: 'id' });
  return { verifier, cookiePrefix: `CognitoIdentityServiceProvider.${config.clientId}.` };
});

export function findIdToken(request: CloudFrontRequest, cookiePrefix: string): string | undefined {
  for (const header of request.headers.cookie ?? []) {
    for (const part of header.value.split(';')) {
      const eq = part.indexOf('=');
      if (eq < 0) continue;
      const name = part.slice(0, eq).trim();
      if (name.startsWith(cookiePrefix) && name.endsWith('.idToken')) {
        return decodeURIComponent(part.slice(eq + 1).trim());
      }
    }
  }
  return undefined;
}

const unauthorized = (status = '401', message = 'Unauthorized'): CloudFrontRequestResult => ({
  status,
  headers: {
    'content-type': [{ key: 'Content-Type', value: 'application/json' }],
    'cache-control': [{ key: 'Cache-Control', value: 'no-store' }],
  },
  body: JSON.stringify({ message }),
});

export const handler = async (event: CloudFrontRequestEvent): Promise<CloudFrontRequestResult> => {
  const request = event.Records[0].cf.request;
  let gate;
  try {
    gate = await getVerifier();
  } catch (err) {
    console.error('API gate config failed', err);
    return unauthorized('503', 'Service unavailable');
  }
  const token = findIdToken(request, gate.cookiePrefix);
  if (!token) return unauthorized();
  try {
    await gate.verifier.verify(token);
  } catch {
    return unauthorized();
  }
  request.headers.authorization = [{ key: 'Authorization', value: token }];
  delete request.headers.cookie; // the API needs only the verified token
  return request;
};
