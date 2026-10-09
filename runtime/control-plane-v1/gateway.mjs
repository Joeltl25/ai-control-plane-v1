import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const CFG = {
  host: process.env.CONTROL_PLANE_V1_HOST || '127.0.0.1',
  port: Number(process.env.CONTROL_PLANE_V1_PORT || 8798),
  stateDir: process.env.CONTROL_PLANE_V1_STATE || '/home/aioperator/control-plane/state/control-plane-v1',
  airtablePatFile: process.env.AIRTABLE_PAT_FILE || '/home/aioperator/.config/ops-dashboard-credentials/AIRTABLE_PAT',
  slackSigningSecretFile: process.env.SLACK_SIGNING_SECRET_FILE || '/home/aioperator/worker/credentials/ai-control-plane/slack-signing-secret',
  approvedUser: process.env.CONTROL_PLANE_APPROVED_USER || 'U0C2MN0DU49',
  approvedChannel: process.env.CONTROL_PLANE_APPROVED_CHANNEL || 'C0C7UTEHR4K',
  baseId: process.env.CONTROL_PLANE_AIRTABLE_BASE || 'apptauaPVwi4qo1EL',
  jobsTable: process.env.CONTROL_PLANE_JOBS_TABLE || 'tbl6wejFI9zqisxL3',
  changeLogTable: process.env.CONTROL_PLANE_CHANGE_LOG_TABLE || 'tblocfVQMPF7nuMBP'
};

const ALLOWED_COMMANDS = new Set([
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

function readSecret(file) {
  try {
    const value = fs.readFileSync(file, 'utf8').trim();
    return value || null;
  } catch {
    return null;
  }
}

function writeJson(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
}

function writeSlack(res, status, text) {
  writeJson(res, status, { response_type: 'ephemeral', text });
}

function verifySlackRequest(req, rawBody) {
  const secret = readSecret(CFG.slackSigningSecretFile);
  if (!secret) return { ok: false, error: 'SLACK_SIGNING_SECRET_MISSING' };

  const timestamp = String(req.headers['x-slack-request-timestamp'] || '');
  const signature = String(req.headers['x-slack-signature'] || '');
  if (!timestamp || !signature) return { ok: false, error: 'SLACK_SIGNATURE_HEADERS_MISSING' };

  const age = Math.abs(Math.floor(Date.now() / 1000) - Number(timestamp));
  if (!Number.isFinite(age) || age > 300) return { ok: false, error: 'SLACK_SIGNATURE_TIMESTAMP_INVALID' };

  const base = `v0:${timestamp}:${rawBody}`;
  const digest = 'v0=' + crypto.createHmac('sha256', secret).update(base).digest('hex');
  const actual = Buffer.from(digest);
  const expected = Buffer.from(signature);
  if (actual.length !== expected.length || !crypto.timingSafeEqual(actual, expected)) {
    return { ok: false, error: 'SLACK_SIGNATURE_INVALID' };
  }

  return { ok: true };
}

function parsePayload(rawBody, contentType) {
  if (contentType.includes('application/json')) return JSON.parse(rawBody || '{}');
  const params = new URLSearchParams(rawBody);
  const out = {};
  for (const [key, value] of params.entries()) out[key] = value;
  return out;
}

function commandText(payload) {
  return String(payload.text || payload.command || '').trim().replace(/^\//, '').toUpperCase();
}

function saveAudit(event) {
  const dir = path.join(CFG.stateDir, 'commands');
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const safeTime = new Date().toISOString().replace(/[:.]/g, '');
  const file = path.join(dir, `${safeTime}-${crypto.randomUUID()}.json`);
  fs.writeFileSync(file, JSON.stringify(event, null, 2) + '\n', { mode: 0o600 });
  return file;
}

async function airtable(method, table, recordId, body) {
  const token = readSecret(CFG.airtablePatFile);
  if (!token) throw new Error('AIRTABLE_PAT_MISSING');

  const url = `https://api.airtable.com/v0/${CFG.baseId}/${table}${recordId ? `/${recordId}` : ''}`;
  const response = await fetch(url, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      'content-type': 'application/json'
    },
    body: body ? JSON.stringify(body) : undefined
  });

  const text = await response.text();
  if (!response.ok) throw new Error(`AIRTABLE_${response.status}_${text.slice(0, 160)}`);
  return text ? JSON.parse(text) : {};
}

async function logChange(command, result) {
  await airtable('POST', CFG.changeLogTable, '', {
    records: [{
      fields: {
        Update: `Slack command ${command}`,
        Layer: 'VPS',
        Status: 'Applied',
        Reason: 'Approved Slack command received by durable gateway.',
        Change: result,
        'Source / Trigger': 'control-plane-v1-gateway',
        'Last Reviewed': new Date().toISOString().slice(0, 10)
      }
    }],
    typecast: true
  });
}

async function handleAllowedCommand(command) {
  switch (command) {
    case 'STATUS':
      await logChange(command, 'STATUS command accepted. Job status query route is available once Airtable record lookup is attached.');
      return 'STATUS accepted. Durable gateway is alive.';
    case 'PLAN':
      await logChange(command, 'PLAN accepted. Planner should create draft tickets only.');
      return 'PLAN accepted. Draft tickets only.';
    case 'APPROVE PLAN':
      await logChange(command, 'APPROVE PLAN accepted. Approved draft tickets can move into queue.');
      return 'APPROVE PLAN accepted.';
    case 'CODE':
      await logChange(command, 'CODE accepted. Worker must claim one ready ticket with one timed lease.');
      return 'CODE accepted. One job, one branch, one worker.';
    case 'APPROVE':
      await logChange(command, 'APPROVE accepted. Merge still requires PR re-fetch and expected head SHA protection.');
      return 'APPROVE accepted. Merge guard remains active.';
    case 'RETRY':
      await logChange(command, 'RETRY accepted. Same route only, bounded by attempts.');
      return 'RETRY accepted.';
    case 'SWITCH':
      await logChange(command, 'SWITCH accepted. Old lease must be released or expired before new worker starts.');
      return 'SWITCH accepted.';
    case 'RESET':
      await logChange(command, 'RESET accepted. Return to rollback point only; no broad delete.');
      return 'RESET accepted.';
    case 'STOP':
      await logChange(command, 'STOP accepted. Pause job safely and release or expire lease.');
      return 'STOP accepted.';
    case 'DISCUSS':
      await logChange(command, 'DISCUSS accepted. Prepare reasoning packet only; do not execute.');
      return 'DISCUSS accepted.';
    default:
      return 'Unknown command.';
  }
}

async function handlePayload(payload) {
  const command = commandText(payload);
  const user = String(payload.user_id || payload.user || '').trim();
  const channel = String(payload.channel_id || payload.channel || '').trim();

  if (user !== CFG.approvedUser) return { status: 403, text: 'Rejected: not approved user.' };
  if (channel !== CFG.approvedChannel) return { status: 403, text: 'Rejected: not approved channel.' };
  if (!ALLOWED_COMMANDS.has(command)) return { status: 400, text: 'Allowed: STATUS, PLAN, APPROVE PLAN, CODE, APPROVE, RETRY, SWITCH, RESET, STOP, DISCUSS.' };

  saveAudit({ receivedAt: new Date().toISOString(), command, user, channel });
  const text = await handleAllowedCommand(command);
  return { status: 200, text };
}

const server = http.createServer(async (req, res) => {
  const chunks = [];
  req.on('data', chunk => chunks.push(chunk));
  req.on('end', async () => {
    try {
      const rawBody = Buffer.concat(chunks).toString('utf8');

      if (req.url === '/health') return writeJson(res, 200, { ok: true, service: 'control-plane-v1', host: CFG.host, port: CFG.port });
      if (req.method !== 'POST' || req.url !== '/slack/command') return writeJson(res, 404, { ok: false, error: 'NOT_FOUND' });

      if (process.env.CONTROL_PLANE_SKIP_SLACK_VERIFY !== '1') {
        const verified = verifySlackRequest(req, rawBody);
        if (!verified.ok) return writeSlack(res, 401, `Rejected: ${verified.error}`);
      }

      const payload = parsePayload(rawBody, String(req.headers['content-type'] || ''));
      const result = await handlePayload(payload);
      return writeSlack(res, result.status, result.text);
    } catch (error) {
      return writeSlack(res, 500, `Gateway error: ${String(error.message || error).slice(0, 200)}`);
    }
  });
});

server.listen(CFG.port, CFG.host, () => {
  console.log(JSON.stringify({ event: 'control_plane_v1_started', host: CFG.host, port: CFG.port }));
});
