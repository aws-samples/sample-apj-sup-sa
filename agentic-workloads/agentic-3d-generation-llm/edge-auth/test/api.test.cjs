// Runs against the bundle: npm run build && npm test
const test = require('node:test');
const assert = require('node:assert');
const { findIdToken } = require('../dist/api/index.js');

const req = (cookie) => ({ headers: cookie ? { cookie: [{ key: 'Cookie', value: cookie }] } : {} });
const prefix = 'CognitoIdentityServiceProvider.abc.';

test('finds the id token cookie for this client', () => {
  const c = `other=1; ${prefix}user%40x.accessToken=AAA; ${prefix}user%40x.idToken=ID.TOKEN; z=2`;
  assert.strictEqual(findIdToken(req(c), prefix), 'ID.TOKEN');
});

test('ignores id tokens issued to another client', () => {
  assert.strictEqual(findIdToken(req('CognitoIdentityServiceProvider.other.u.idToken=X'), prefix), undefined);
});

test('no cookie header', () => {
  assert.strictEqual(findIdToken(req(), prefix), undefined);
});
