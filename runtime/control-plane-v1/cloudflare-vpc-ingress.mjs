// Cloudflare Workers VPC gateway for the existing Slack /ai command.
// No credentials here. The private VPS gateway verifies Slack HMAC and user/channel.
const PRIVATE_URL = 'http://localhost:8798/slack/command';

const reply = (status, text) => new Response(text, {
  status,
  headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' },
});

export async function handleRequest(request, env) {
  let url;
  try { url = new URL(request.url); }
  catch { return reply(404, 'Not found'); }

  // Expose exactly one authenticated-by-origin POST path.
  if (request.method !== 'POST' || url.pathname !== '/slack/command' || url.search) {
    return reply(404, 'Not found');
  }

  // Cheap prefilter only: HMAC verification remains exclusively on the VPS.
  const signature = request.headers.get('x-slack-signature') || '';
  const timestamp = request.headers.get('x-slack-request-timestamp') || '';
  if (!/^v0=[0-9a-f]{64}$/.test(signature) || !/^[0-9]{10,}$/.test(timestamp)) {
    return reply(401, 'Unauthorized');
  }

  if (!env?.VPS_GATEWAY || typeof env.VPS_GATEWAY.fetch !== 'function') {
    return reply(503, 'Gateway not ready');
  }

  try {
    // Preserve the original request stream and headers byte-for-byte for
    // the existing VPS Slack signature verifier. Never log payloads.
    const forward = new Request(PRIVATE_URL, request);
    return await env.VPS_GATEWAY.fetch(forward, {
      signal: AbortSignal.timeout(2500),
      redirect: 'manual',
    });
  } catch {
    return reply(502, 'Gateway unavailable');
  }
}

export default { fetch: handleRequest };
