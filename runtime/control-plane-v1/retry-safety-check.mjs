import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const allowed = new Set([
  'STATUS',
  'PLAN',
  'APPROVE PLAN',
  'CODE',
  'APPROVE',
  'RETRY',
  'SWITCH',
  'RESET',
  'STOP',
  'DISCUSS'
]);

function normalize(payload) {
  return String(payload.text || payload.command || '').trim().replace(/^\//, '').toUpperCase();
}

function receiptKey(payload, headers = {}) {
  const command = normalize(payload);
  const user = String(payload.user_id || payload.user || '').trim();
  const channel = String(payload.channel_id || payload.channel || '').trim();
  const timestamp = String(headers['x-slack-request-timestamp'] || '');
  const signature = String(headers['x-slack-signature'] || '');
  const trigger = String(payload.trigger_id || payload.response_url || '').trim();
  return crypto.createHash('sha256').update(JSON.stringify({ command, user, channel, timestamp, signature, trigger })).digest('hex');
}

const first = {
  text: ' approve ',
  user_id: 'U0C2MN0DU49',
  channel_id: 'C0C7UTEHR4K',
  trigger_id: 'same-event'
};

const retry = { ...first };
const headers = {
  'x-slack-request-timestamp': '1791569000',
  'x-slack-signature': 'v0=test'
};

assert.equal(normalize({ text: '/status' }), 'STATUS');
assert.equal(allowed.has(normalize(first)), true);
assert.equal(receiptKey(first, headers), receiptKey(retry, headers));
assert.notEqual(receiptKey(first, headers), receiptKey({ ...first, text: 'CODE' }, headers));
assert.notEqual(receiptKey(first, headers), receiptKey({ ...first, trigger_id: 'next-event' }, headers));

console.log('PASS retry safety contract');
