import assert from 'node:assert/strict';
import test from 'node:test';
import { runSmoke } from '../apps/web/scripts/smoke-aws-api.mjs';

function fixture() {
  return {
    apiUrl: 'https://example.execute-api.us-east-1.amazonaws.com',
    userPoolId: 'us-east-1_abcdefgh', userPoolClientId: 'client',
    users: Object.fromEntries(['admin_a', 'viewer_a', 'admin_b'].map(name => [name,
      { username: name, password: 'secret-password', sub: name } ])),
    fixtures: { tenantA: 'a', tenantB: 'b', jobId: 'job', reportId: 'report',
      observationId: 'receipt', proofProductId: 'proof' },
  };
}

test('uses SRP and records expected real HTTP checks without tokens or passwords', async () => {
  const config = fixture();
  const signed = [];
  const auth = { signOut: async () => {}, signIn: async input => {
    signed.push(input); return { isSignedIn: true };
  }, fetchAuthSession: async () => ({ tokens: { idToken: 'secret-token' } }) };
  const statuses = [200, 200, 200, 401, 401, 403, 403, 404, 404, 403, 201, 200, 200, 200, 403];
  const bodies = [
    { tenant_id: 'a', role: 'admin' }, { tenant_id: 'b', role: 'admin' },
    { tenant_id: 'a', role: 'viewer' }, {}, {}, {}, {}, {}, {}, {},
    { tenant_id: 'a' }, { products: [{ product_id: 'proof' }] }, { products: [] }, {}, {},
  ];
  let i = 0;
  const http = async () => ({ status: statuses[i], json: async () => bodies[i++] });
  const result = await runSmoke(config, auth, http);
  assert.equal(result.status, 'passed');
  assert.equal(result.checks.length, 15);
  assert.equal(signed.length, 3);
  assert.ok(signed.every(input => input.options.authFlowType === 'USER_SRP_AUTH'));
  assert.ok(!JSON.stringify(result).includes('secret-token'));
  assert.ok(!JSON.stringify(result).includes('secret-password'));
});

test('unexpected API status fails the smoke evidence', async () => {
  const auth = { signOut: async () => {}, signIn: async () => ({ isSignedIn: true }),
    fetchAuthSession: async () => ({ tokens: { idToken: 'secret-token' } }) };
  const result = await runSmoke(fixture(), auth, async () => ({ status: 500, json: async () => ({}) }));
  assert.equal(result.status, 'failed');
  assert.ok(result.checks.every(check => !check.passed));
});

test('rejects a non-AWS endpoint before sign-in', async () => {
  await assert.rejects(runSmoke({ ...fixture(), apiUrl: 'https://example.com' }), /unexpected_api_endpoint/);
});
