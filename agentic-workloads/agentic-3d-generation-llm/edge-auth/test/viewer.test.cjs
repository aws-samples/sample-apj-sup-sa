const test = require('node:test');
const assert = require('node:assert');
const { fixPostLoginRedirect, rewriteSpaRoute } = require('../dist/viewer/index.js');

const host = 'd123.cloudfront.net';
const res = (loc) => ({ status: '302', headers: { location: [{ key: 'Location', value: loc }] } });
const loc = (r) => r.headers.location[0].value;

test('collapses the doubled host after sign-in', () => {
  assert.strictEqual(loc(fixPostLoginRedirect(res(`https://${host}/https://${host}`), host)), `https://${host}/`);
  assert.strictEqual(loc(fixPostLoginRedirect(res(`https://${host}/https://${host}/?job=1`), host)), `https://${host}/?job=1`);
});

test('leaves normal same-host paths alone', () => {
  assert.strictEqual(loc(fixPostLoginRedirect(res(`https://${host}/assets/x.js`), host)), `https://${host}/assets/x.js`);
});

test('never redirects to another host', () => {
  assert.strictEqual(loc(fixPostLoginRedirect(res(`https://${host}/https://evil.example`), host)), `https://${host}/`);
  assert.strictEqual(new URL(loc(fixPostLoginRedirect(res(`https://${host}//evil.example`), host))).host, host);
});

test('does not touch redirects to Cognito or plain requests', () => {
  const cognito = 'https://x.auth.us-east-1.amazoncognito.com/authorize?a=1';
  assert.strictEqual(loc(fixPostLoginRedirect(res(cognito), host)), cognito);
  const req = { uri: '/', headers: {} };
  assert.strictEqual(fixPostLoginRedirect(req, host), req);
});

test('sign-out goes through Cognito /logout and keeps the cookie-clearing headers', () => {
  const { cognitoLogoutRedirect } = require('../dist/viewer/index.js');
  const cleared = { status: '302', headers: {
    location: [{ key: 'Location', value: `https://${host}` }],
    'set-cookie': [{ key: 'Set-Cookie', value: 'CognitoIdentityServiceProvider.c.u.idToken=; Expires=Thu, 01 Jan 1970 00:00:00 GMT' }],
  } };
  const r = cognitoLogoutRedirect(cleared, host, 'x.auth.us-east-1.amazoncognito.com', 'client123');
  const url = new URL(loc(r));
  assert.strictEqual(url.origin + url.pathname, 'https://x.auth.us-east-1.amazoncognito.com/logout');
  assert.strictEqual(url.searchParams.get('client_id'), 'client123');
  assert.strictEqual(url.searchParams.get('logout_uri'), `https://${host}`);
  assert.strictEqual(r.headers['set-cookie'].length, 1);
});

test('sign-out without site cookies still ends the Cognito session', () => {
  const { cognitoLogoutRedirect } = require('../dist/viewer/index.js');
  const r = cognitoLogoutRedirect(undefined, host, 'x.auth.us-east-1.amazoncognito.com', 'c');
  assert.ok(loc(r).startsWith('https://x.auth.us-east-1.amazoncognito.com/logout?'));
});

test('after sign-in, app routes serve index.html; files and generated GLBs do not', () => {
  const uri = (u) => rewriteSpaRoute({ uri: u, headers: {} }).uri;
  assert.strictEqual(uri('/gallery'), '/index.html');
  assert.strictEqual(uri('/gallery/0b3f5a8e-6d1c-4c3e-9a51-1b2a3c4d5e6f'), '/index.html');
  assert.strictEqual(uri('/how-it-works'), '/index.html');
  assert.strictEqual(uri('/'), '/');
  assert.strictEqual(uri('/assets/index-abc.js'), '/assets/index-abc.js');
  assert.strictEqual(uri('/generations/j/m/model.glb'), '/generations/j/m/model.glb');
  assert.strictEqual(uri('/generations/j/input'), '/generations/j/input');
});

test('a sign-in redirect is never rewritten', () => {
  const redirect = res('https://x.auth.us-east-1.amazoncognito.com/authorize?a=1');
  assert.strictEqual(rewriteSpaRoute(redirect), redirect);
});
