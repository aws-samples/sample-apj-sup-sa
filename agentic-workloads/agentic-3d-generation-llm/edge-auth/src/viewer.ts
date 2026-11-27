// Viewer-request gate for every page and asset: no valid Cognito session -> redirect to the
// Cognito managed login before any content is served. Uses awslabs/cognito-at-edge.
import type { CloudFrontRequestEvent, CloudFrontRequestResult } from 'aws-lambda';
import { Authenticator } from 'cognito-at-edge';
import { NONCE_SECRET_ID, loadConfig, memo, readSecret } from './config';

const SIGNOUT_PATH = '/signout';

const getGate = memo(async () => {
  const [config, nonceSigningSecret] = await Promise.all([loadConfig(), readSecret(NONCE_SECRET_ID)]);
  const authenticator = new Authenticator({
    region: config.region,
    userPoolId: config.userPoolId,
    userPoolAppId: config.clientId,
    userPoolAppSecret: config.clientSecret,
    userPoolDomain: config.domain,
    cookieExpirationDays: 1,
    httpOnly: true, // tokens never reach page JavaScript
    sameSite: 'Lax',
    cookiePath: '/',
    logLevel: 'warn',
    csrfProtection: { nonceSigningSecret },
    // Empty logoutRedirectUri: the library falls back to https://<host>, which is registered on the client.
    logoutConfiguration: { logoutUri: SIGNOUT_PATH, logoutRedirectUri: '' },
  });
  return { authenticator, config };
});

/**
 * cognito-at-edge's sign-out revokes the tokens and clears this site's cookies, but redirects straight
 * back to the site, so Cognito's own session cookie survives and the next /authorize signs the user
 * in again silently. Send the browser through Cognito's /logout as well; it returns to https://host,
 * which is the client's registered sign-out URL, and the gate then shows the login page.
 */
export function cognitoLogoutRedirect(result: CloudFrontRequestResult, host: string, domain: string, clientId: string): CloudFrontRequestResult {
  const logoutUrl = `https://${domain}/logout?${new URLSearchParams({ client_id: clientId, logout_uri: `https://${host}` })}`;
  const response = result && 'status' in result ? result : { status: '302', headers: {} };
  return {
    ...response,
    status: '302',
    statusDescription: 'Found',
    headers: {
      ...response.headers,
      location: [{ key: 'Location', value: logoutUrl }],
      'cache-control': [{ key: 'Cache-Control', value: 'no-store' }],
    },
  };
}

/**
 * cognito-at-edge 1.5.5 with csrfProtection stores the full callback URL (https://host) in the
 * state and then redirects to "https://host/" + that URL, i.e. https://host/https://host.
 * Collapse that to a path on the same host; anything else stays on this host too.
 */
export function fixPostLoginRedirect(result: CloudFrontRequestResult, host: string): CloudFrontRequestResult {
  const location = result && 'status' in result ? result.headers?.location?.[0] : undefined;
  if (!location || !host) return result;
  const prefix = `https://${host}/`;
  if (!location.value.startsWith(prefix)) return result;
  let rest = location.value.slice(prefix.length);
  const doubled = `https://${host}`;
  if (rest.startsWith(doubled)) rest = rest.slice(doubled.length);
  if (/^[a-z][a-z0-9+.-]*:/i.test(rest) || rest.startsWith('//')) rest = ''; // never leave this host
  location.value = prefix + rest.replace(/^\/+/, '');
  return result;
}

/**
 * Client-side routes (/gallery, /gallery/<jobId>, /how-it-works) have no file in S3: serve the app.
 * Runs only after sign-in has passed, on the request the gate lets through. Generated files under
 * /generations/ are real objects and are never rewritten.
 */
export function rewriteSpaRoute(result: CloudFrontRequestResult): CloudFrontRequestResult {
  if (!result || 'status' in result || !('uri' in result)) return result; // a redirect or error, not a request
  const uri = result.uri;
  const last = uri.split('/').pop() ?? '';
  if (uri !== '/' && !uri.startsWith('/generations/') && !last.includes('.')) result.uri = '/index.html';
  return result;
}

export const handler = async (event: CloudFrontRequestEvent): Promise<CloudFrontRequestResult> => {
  const request = event.Records[0].cf.request;
  const host = request.headers.host?.[0]?.value ?? '';
  try {
    const { authenticator, config } = await getGate();
    if (request.uri === SIGNOUT_PATH) {
      // The library's sign-out assumes a Cookie header; with none there are no site cookies to clear.
      const cleared = request.headers.cookie?.length ? ((await authenticator.handle(event)) as CloudFrontRequestResult) : undefined;
      return cognitoLogoutRedirect(cleared, host, config.domain, config.clientId);
    }
    const result = (await authenticator.handle(event)) as CloudFrontRequestResult;
    return rewriteSpaRoute(fixPostLoginRedirect(result, host));
  } catch (err) {
    // Fail closed: if the gate can't load its configuration, serve nothing.
    console.error('Edge auth failed', err);
    return {
      status: '503',
      statusDescription: 'Service Unavailable',
      headers: { 'cache-control': [{ key: 'Cache-Control', value: 'no-store' }] },
      body: 'Sign-in is temporarily unavailable.',
    };
  }
};
