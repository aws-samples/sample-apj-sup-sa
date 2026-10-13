// Cognito Authentication Service
// OAuth Authorization Code flow for user login via Cognito Hosted UI
// Tokens carry custom:role and custom:account_id for Gateway RBAC

// Runtime config (window.__APP_CONFIG__) is injected by the Amplify deployer
// Lambda in demo mode. Falls back to build-time env vars for local dev.
const RC = (typeof window !== 'undefined' && window.__APP_CONFIG__) || {};

const COGNITO_CONFIG = {
  userClientId: RC.COGNITO_USER_CLIENT_ID || process.env.REACT_APP_COGNITO_USER_CLIENT_ID || '',
  domain: RC.COGNITO_DOMAIN || process.env.REACT_APP_COGNITO_DOMAIN || '',
  scope: RC.COGNITO_SCOPE || process.env.REACT_APP_COGNITO_SCOPE || 'openid profile email',
  // Prefer runtime config, then the ACTUAL serving origin, and only then a
  // build-time env var. window.location must outrank REACT_APP_REDIRECT_URI so a
  // stray .env.local (which CRA also bakes into production builds) can never force
  // a localhost redirect onto the deployed site → Cognito redirect_mismatch.
  redirectUri: RC.REDIRECT_URI || window.location.origin + window.location.pathname || process.env.REACT_APP_REDIRECT_URI,
  region: RC.AWS_REGION || process.env.REACT_APP_AWS_REGION || 'us-east-1',
};

const TOKEN_KEYS = {
  accessToken: 'cognito_access_token',
  idToken: 'cognito_id_token',
  refreshToken: 'cognito_refresh_token',
  tokenExpiry: 'cognito_token_expiry',
  user: 'cognito_user',
};

const parseJwt = (token) => {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(window.atob(base64));
  } catch (e) {
    return null;
  }
};

// OAuth Authorization Code flow URLs
export const getLoginUrl = () => {
  if (!COGNITO_CONFIG.domain || !COGNITO_CONFIG.userClientId) {
    console.warn('Cognito not configured — login disabled');
    return null;
  }
  const params = new URLSearchParams({
    client_id: COGNITO_CONFIG.userClientId,
    response_type: 'code',
    scope: COGNITO_CONFIG.scope,
    redirect_uri: COGNITO_CONFIG.redirectUri,
  });
  return `https://${COGNITO_CONFIG.domain}/login?${params.toString()}`;
};

export const getLogoutUrl = () => {
  const params = new URLSearchParams({
    client_id: COGNITO_CONFIG.userClientId,
    logout_uri: COGNITO_CONFIG.redirectUri,
  });
  return `https://${COGNITO_CONFIG.domain}/logout?${params.toString()}`;
};

// Exchange authorization code for tokens
export const exchangeCodeForTokens = async (code) => {
  const tokenUrl = `https://${COGNITO_CONFIG.domain}/oauth2/token`;
  const response = await fetch(tokenUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: COGNITO_CONFIG.userClientId,
      code,
      redirect_uri: COGNITO_CONFIG.redirectUri,
    }),
  });

  if (!response.ok) throw new Error('Failed to exchange code for tokens');
  const tokens = await response.json();

  // Store tokens
  localStorage.setItem(TOKEN_KEYS.accessToken, tokens.access_token);
  localStorage.setItem(TOKEN_KEYS.idToken, tokens.id_token);
  if (tokens.refresh_token) localStorage.setItem(TOKEN_KEYS.refreshToken, tokens.refresh_token);
  localStorage.setItem(TOKEN_KEYS.tokenExpiry, (Date.now() + tokens.expires_in * 1000).toString());

  // Parse user info from ID token
  const claims = parseJwt(tokens.id_token);
  if (claims) {
    localStorage.setItem(TOKEN_KEYS.user, JSON.stringify({
      userId: claims.sub,
      username: claims.email || claims['cognito:username'],
      name: claims.name || claims.email,
      email: claims.email,
      role: claims['custom:role'] || 'user',
      businessId: claims['custom:account_id'] || '',
    }));
  }
  return tokens;
};

// Handle OAuth callback (check for code in URL)
export const handleAuthCallback = async () => {
  const code = new URLSearchParams(window.location.search).get('code');
  if (code) {
    try {
      await exchangeCodeForTokens(code);
      window.history.replaceState({}, document.title, window.location.pathname);
      return true;
    } catch (e) {
      console.error('Auth callback failed:', e);
      // Clean up the code from URL even on failure
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }
  return false;
};

export const isAuthenticated = () => {
  const expiry = localStorage.getItem(TOKEN_KEYS.tokenExpiry);
  return expiry ? Date.now() < parseInt(expiry, 10) : false;
};

export const getCurrentUser = () => {
  const userStr = localStorage.getItem(TOKEN_KEYS.user);
  if (userStr) return JSON.parse(userStr);
  return { userId: 'anonymous', username: 'Guest', name: 'Guest User', role: 'guest', businessId: '' };
};

export const isAdmin = () => getCurrentUser().role === 'rental_admin';

// Refresh tokens using stored refresh_token
const refreshTokens = async () => {
  const refreshToken = localStorage.getItem(TOKEN_KEYS.refreshToken);
  if (!refreshToken) return false;
  try {
    const response = await fetch(`https://${COGNITO_CONFIG.domain}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'refresh_token',
        client_id: COGNITO_CONFIG.userClientId,
        refresh_token: refreshToken,
      }),
    });
    if (!response.ok) return false;
    const tokens = await response.json();
    localStorage.setItem(TOKEN_KEYS.accessToken, tokens.access_token);
    localStorage.setItem(TOKEN_KEYS.idToken, tokens.id_token);
    localStorage.setItem(TOKEN_KEYS.tokenExpiry, (Date.now() + tokens.expires_in * 1000).toString());
    return true;
  } catch (e) {
    console.error('Token refresh failed:', e);
    return false;
  }
};

// Get OAuth access token for Gateway RBAC (passed as gateway_token to agent)
export const fetchAccessToken = async () => {
  if (!isAuthenticated()) {
    const refreshed = await refreshTokens();
    if (!refreshed) return null;
  }
  return localStorage.getItem(TOKEN_KEYS.accessToken);
};

// Get ID token for Identity Pool authenticated flow
export const fetchIdToken = () => {
  if (!isAuthenticated()) return null;
  return localStorage.getItem(TOKEN_KEYS.idToken);
};

export const logout = () => {
  Object.values(TOKEN_KEYS).forEach(key => localStorage.removeItem(key));
  const logoutUrl = getLogoutUrl();
  if (logoutUrl) window.location.href = logoutUrl;
};

export const clearToken = () => {
  Object.values(TOKEN_KEYS).forEach(key => localStorage.removeItem(key));
};

export const login = () => {
  const loginUrl = getLoginUrl();
  if (loginUrl) window.location.href = loginUrl;
  else console.warn('Login not available — COGNITO_USER_CLIENT_ID and COGNITO_DOMAIN not configured');
};

// Login is available when Cognito user client is configured (set by deploy_policy.py)
export const isLoginAvailable = () => !!COGNITO_CONFIG.domain && !!COGNITO_CONFIG.userClientId;

export default {
  getLoginUrl, getLogoutUrl, exchangeCodeForTokens, handleAuthCallback,
  isAuthenticated, getCurrentUser, isAdmin, fetchAccessToken, fetchIdToken,
  logout, clearToken, login, isLoginAvailable,
};
