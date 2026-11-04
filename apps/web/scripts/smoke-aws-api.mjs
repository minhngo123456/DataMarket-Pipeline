// Credentials enter through stdin; stdout contains only check names and HTTP statuses.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Amplify } from 'aws-amplify';
import { fetchAuthSession, signIn, signOut } from 'aws-amplify/auth';

export async function runSmoke(config, auth = { signIn, signOut, fetchAuthSession }, http = fetch) {
  const api = new URL(config.apiUrl);
  if (api.protocol !== 'https:' || !/^[a-z0-9-]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?$/.test(api.hostname)) {
    throw new Error('unexpected_api_endpoint');
  }
  Amplify.configure({ Auth: { Cognito: {
    userPoolId: config.userPoolId,
    userPoolClientId: config.userPoolClientId,
    loginWith: { email: true },
  } } });
  const checks = [];
  const tokens = {};
  async function request(name, who, method, path, expected, body, verify = () => true) {
    const headers = { 'Content-Type': 'application/json' };
    if (who) headers.Authorization = `Bearer ${tokens[who] ?? 'invalid-token'}`;
    const response = await http(new URL(path, api), {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(20000),
    });
    let data = {};
    try { data = await response.json(); } catch { /* Status alone is sufficient for gateway errors. */ }
    const passed = response.status === expected && verify(data);
    checks.push({ name, passed, expected_status: expected, actual_status: response.status });
    return data;
  }
  for (const [name, user] of Object.entries(config.users)) {
    await auth.signOut();
    const signed = await auth.signIn({
      username: user.username, password: user.password,
      options: { authFlowType: 'USER_SRP_AUTH' },
    });
    if (!signed.isSignedIn) throw new Error('unexpected_sign_in_challenge');
    const session = await auth.fetchAuthSession();
    if (!session.tokens?.idToken) throw new Error('missing_id_token');
    tokens[name] = session.tokens.idToken.toString();
  }
  const f = config.fixtures;
  await request('admin_a_identity', 'admin_a', 'GET', '/me', 200, undefined,
    data => data.tenant_id === f.tenantA && data.role === 'admin');
  await request('admin_b_identity', 'admin_b', 'GET', '/me', 200, undefined,
    data => data.tenant_id === f.tenantB && data.role === 'admin');
  await request('viewer_identity', 'viewer_a', 'GET', '/me', 200, undefined,
    data => data.tenant_id === f.tenantA && data.role === 'viewer');
  await request('gateway_rejects_unsigned_request', null, 'GET', '/me', 401);
  await request('gateway_rejects_invalid_jwt', 'invalid', 'GET', '/me', 401);
  await request('viewer_cannot_create_product', 'viewer_a', 'POST', '/products', 403,
    { product_id: 'forbidden', name: 'Must not be created' });
  await request('viewer_cannot_upload', 'viewer_a', 'POST', '/jobs', 403, { source_id: 'vendor' });
  await request('tenant_b_cannot_read_a_job', 'admin_b', 'GET', `/jobs/${f.jobId}/status`, 404);
  await request('tenant_b_cannot_read_a_report', 'admin_b', 'GET', `/reports/${f.reportId}`, 404,
    undefined, data => !data.download_url);
  await request('tenant_b_cannot_approve_a_receipt', 'admin_b', 'PATCH', '/products/phone/match', 403,
    { observation_id: f.observationId, product_id: 'phone', tenant_id: f.tenantA });
  await request('body_tenant_is_ignored', 'admin_a', 'POST', '/products', 201,
    { product_id: f.proofProductId, name: 'Tenant isolation proof', tenant_id: f.tenantB },
    data => data.tenant_id === f.tenantA);
  await request('product_is_visible_to_owner', 'admin_a', 'GET', '/products', 200, undefined,
    data => (data.products ?? []).some(p => p.product_id === f.proofProductId));
  await request('product_is_hidden_from_other_tenant', 'admin_b', 'GET', '/products', 200, undefined,
    data => !(data.products ?? []).some(p => p.product_id === f.proofProductId));
  await request('admin_can_disable_viewer', 'admin_a', 'PATCH', `/members/${config.users.viewer_a.sub}`, 200,
    { status: 'disabled' });
  await request('disabled_member_cannot_use_existing_jwt', 'viewer_a', 'GET', '/me', 403);
  await auth.signOut();
  return { mode: 'real_cognito_srp_and_http_api', status: checks.every(c => c.passed) ? 'passed' : 'failed', checks };
}

async function main() {
  if (process.argv.includes('--help')) {
    console.log('Read pool/client/API/users/fixtures JSON from stdin. Use python -m scripts.smoke_api_aws to create isolated fixtures.');
    return;
  }
  try {
    const result = await runSmoke(JSON.parse(readFileSync(0, 'utf8')));
    console.log(JSON.stringify(result));
    process.exitCode = result.status === 'passed' ? 0 : 1;
  } catch (error) {
    // Do not print upstream messages, request objects, passwords or tokens.
    console.log(JSON.stringify({ status: 'failed', error_type: error.name ?? 'Error' }));
    process.exitCode = 1;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}
