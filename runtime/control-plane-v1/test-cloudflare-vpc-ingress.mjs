import test from 'node:test';
import assert from 'node:assert/strict';
import { handleRequest } from './cloudflare-vpc-ingress.mjs';

const WORKER_URL = 'https://ai-control-plane-slack-ingress.joeltl25.workers.dev/slack/command';
const body = 'command=%2Fai&text=STATUS%20%26%20CHECK&user_id=U0C2MN0DU49';
const signedHeaders = {
  'x-slack-signature': 'v0=' + 'a'.repeat(64),
  'x-slack-request-timestamp': '1791663000',
  'content-type': 'application/x-www-form-urlencoded',
};
function request(url = WORKER_URL, options = {}) {
  return new Request(url, { method: 'POST', headers: signedHeaders, body, ...options });
}

test('denies every path and method except exact POST /slack/command', async () => {
  let called = false;
  const env = { VPS_GATEWAY: { fetch: async () => { called = true; return new Response('wrong'); } } };
  for (const u of [WORKER_URL + '?x=1', WORKER_URL.replace('/slack/command', '/health'), WORKER_URL + '/other']) {
    assert.equal((await handleRequest(request(u), env)).status, 404);
  }
  assert.equal((await handleRequest(new Request(WORKER_URL), env)).status, 404);
  assert.equal(called, false);
});

test('rejects missing or malformed signature without forwarding', async () => {
  let called = false;
  const env = { VPS_GATEWAY: { fetch: async () => { called = true; return new Response('wrong'); } } };
  for (const headers of [{}, {'x-slack-signature':'v0=abc'}, {'x-slack-signature':signedHeaders['x-slack-signature']}]) {
    assert.equal((await handleRequest(request(WORKER_URL, {headers}), env)).status, 401);
  }
  assert.equal(called, false);
});

test('fails closed if private binding is absent', async () => {
  const result = await handleRequest(request(), {});
  assert.equal(result.status, 503);
  assert.equal(await result.text(), 'Gateway not ready');
});

test('preserves Slack headers, request body and response through private VPC binding', async () => {
  let called = 0;
  const env = { VPS_GATEWAY: { fetch: async (received) => {
    called++;
    assert.equal(received.url, 'http://localhost:8798/slack/command');
    assert.equal(received.method, 'POST');
    assert.equal(received.headers.get('x-slack-signature'), signedHeaders['x-slack-signature']);
    assert.equal(received.headers.get('x-slack-request-timestamp'), signedHeaders['x-slack-request-timestamp']);
    assert.equal(await received.text(), body);
    return new Response('JOB-0013: Needs Worker', { status: 200, headers: { 'content-type':'text/plain' } });
  } } };
  const response = await handleRequest(request(), env);
  assert.equal(called, 1);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), 'JOB-0013: Needs Worker');
});

test('passes through private gateway rejection rather than bypassing it', async () => {
  const env = { VPS_GATEWAY: { fetch: async () => new Response('Rejected', { status: 403 }) } };
  assert.equal((await handleRequest(request(), env)).status, 403);
});

test('returns generic unavailable status on private binding failure', async () => {
  const env = { VPS_GATEWAY: { fetch: async () => { throw new Error('private backend details must not leak'); } } };
  const response = await handleRequest(request(), env);
  assert.equal(response.status, 502);
  assert.equal(await response.text(), 'Gateway unavailable');
});
