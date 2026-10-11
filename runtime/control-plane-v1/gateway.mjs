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
  ticketsTable: process.env.CONTROL_PLANE_TICKETS_TABLE || 'tbl3b9O4aOXzhnmnQ',
  leasesTable: process.env.CONTROL_PLANE_LEASES_TABLE || 'tblkDigmZvdUZIpuQ',
  changeLogTable: process.env.CONTROL_PLANE_CHANGE_LOG_TABLE || 'tblocfVQMPF7nuMBP',
  approvalsTable: process.env.CONTROL_PLANE_APPROVALS_TABLE || 'tbly8EGid6CbGaHVr',
  projectsTable: process.env.CONTROL_PLANE_PROJECTS_TABLE || 'tblMJiGB0mS0Rmwjk',
  leaseMinutes: Number(process.env.CONTROL_PLANE_LEASE_MINUTES || 20),
  workerName: process.env.CONTROL_PLANE_WORKER_NAME || 'vps-local-gateway'
};

const ALLOWED_COMMANDS = new Set([
  'STATUS', 'PLAN', 'APPROVE PLAN', 'CODE', 'APPROVE',
  'RETRY', 'SWITCH', 'RESET', 'STOP', 'DISCUSS'
]);

const ACTIVE_STATUS_ORDER = [
  'In Progress', 'Needs Review', 'Needs Credential', 'Needs Repo',
  'Ready', 'Blocked', 'Paused', 'Done'
];

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

function parseCommand(payload) {
  const raw = String(payload.text || '').trim().replace(/^\//, '');
  const normalized = raw.replace(/\s+/g, ' ').trim();
  if (!normalized) return { command: 'STATUS', target: '' };
  const upper = normalized.toUpperCase();
  if (upper === 'APPROVE PLAN' || upper.startsWith('APPROVE PLAN ')) {
    return { command: 'APPROVE PLAN', target: normalized.slice('APPROVE PLAN'.length).trim() };
  }
  const first = normalized.split(' ')[0].toUpperCase();
  const target = normalized.split(' ').slice(1).join(' ').trim();
  return { command: first, target };
}

function short(value, max = 900) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, max);
}

function saveAudit(event) {
  const dir = path.join(CFG.stateDir, 'commands');
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const safeTime = new Date().toISOString().replace(/[:.]/g, '');
  const file = path.join(dir, `${safeTime}-${crypto.randomUUID()}.json`);
  fs.writeFileSync(file, JSON.stringify(event, null, 2) + '\n', { mode: 0o600 });
  return file;
}

function credentialState() {
  return {
    airtable: !!readSecret(CFG.airtablePatFile),
    slackSigningSecret: !!readSecret(CFG.slackSigningSecretFile),
    host: CFG.host,
    port: CFG.port
  };
}

async function airtable(method, table, recordId = '', body, params) {
  const token = readSecret(CFG.airtablePatFile);
  if (!token) throw new Error('AIRTABLE_PAT_MISSING');
  const url = new URL(`https://api.airtable.com/v0/${CFG.baseId}/${table}${recordId ? `/${recordId}` : ''}`);
  for (const [key, value] of Object.entries(params || {})) url.searchParams.set(key, String(value));
  const response = await fetch(url, {
    method,
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`AIRTABLE_${response.status}_${text.slice(0, 160)}`);
  return text ? JSON.parse(text) : {};
}

async function listRecords(table, params = {}) {
  const out = await airtable('GET', table, '', null, params);
  return out.records || [];
}

async function updateRecord(table, recordId, fields) {
  return airtable('PATCH', table, recordId, { fields, typecast: true });
}

async function createRecord(table, fields) {
  return airtable('POST', table, '', { records: [{ fields }], typecast: true });
}

async function logChange(command, result, status = 'Applied') {
  await createRecord(CFG.changeLogTable, {
    Update: `Slack command ${command}`,
    Layer: 'VPS',
    Status: status,
    Reason: 'Approved Slack command received by durable gateway.',
    Change: short(result, 1800),
    'Source / Trigger': 'control-plane-v1-gateway',
    'Last Reviewed': new Date().toISOString().slice(0, 10)
  });
}

function jobPriority(record) {
  const status = record.fields?.Status || '';
  const index = ACTIVE_STATUS_ORDER.indexOf(status);
  return index === -1 ? ACTIVE_STATUS_ORDER.length : index;
}

async function getCurrentJob() {
  const records = await listRecords(CFG.jobsTable, {
    pageSize: 25,
    'sort[0][field]': 'Last Heartbeat',
    'sort[0][direction]': 'desc'
  });
  if (!records.length) return null;
  return records.sort((a, b) => jobPriority(a) - jobPriority(b))[0];
}

function formatJob(record) {
  if (!record) return 'No job records found.';
  const f = record.fields || {};
  const parts = [
    `${f['Job ID'] || record.id}: ${f.Status || 'Unknown'}`,
    `Next: ${short(f['Next Action'] || 'None recorded.', 240)}`
  ];
  if (f['PR URL']) parts.push(`PR: ${f['PR URL']}`);
  if (!credentialState().slackSigningSecret) parts.push('Blocker: Slack signing secret missing.');
  return parts.join('\n');
}

function norm(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

async function listProjects() {
  return listRecords(CFG.projectsTable, {
    pageSize: 50,
    'sort[0][field]': 'Project',
    'sort[0][direction]': 'asc'
  });
}

function projectMatches(record, target) {
  const needle = norm(target);
  if (!needle) return false;
  const f = record.fields || {};
  const candidates = [f.Project, f['GitHub Repo'], f.Purpose, f['Slack Channel']].map(norm).join(' | ');
  return candidates.includes(needle) || needle.split(' ').every(part => candidates.includes(part));
}

function oneLineProject(record) {
  const f = record.fields || {};
  const status = f.Status || 'Unknown';
  const prod = f['Production State'] || 'Unknown';
  const route = short(f['Current Route'] || 'No route recorded.', 95);
  return `• ${f.Project || record.id}: ${status} / ${prod} — ${route}`;
}

async function formatAllProjects(currentJob) {
  const projects = await listProjects();
  const visible = projects.filter(r => String(r.fields?.Status || '') !== 'Archived').slice(0, 12);
  const lines = ['AI control plane status:', '', 'Projects:'];
  if (!visible.length) lines.push('No project registry rows found.');
  for (const record of visible) lines.push(oneLineProject(record));
  lines.push('', 'Top job:', formatJob(currentJob));
  lines.push('', 'Use: /ai STATUS <project>, /ai PLAN <project>, /ai CODE <project>, /ai STOP <project>.');
  return lines.join('\n');
}

async function formatProjectStatus(target, currentJob) {
  if (!target) return formatAllProjects(currentJob);
  const projects = await listProjects();
  const needle = norm(target);
  const record = projects.find(r => norm(r.fields?.Project).includes(needle) || needle.includes(norm(r.fields?.Project))) || projects.find(r => projectMatches(r, target));
  if (!record) return `Project not found: ${target}\nUse /ai STATUS to list registered projects.`;
  const f = record.fields || {};
  const jobs = await listRecords(CFG.jobsTable, { pageSize: 50 });
  const related = jobs.filter(j => norm(j.fields?.['Project Key']).includes(norm(f.Project)) || norm(f.Project).includes(norm(j.fields?.['Project Key']))).slice(0, 3);
  const out = [
    `${f.Project || target}: ${f.Status || 'Unknown'} / ${f['Production State'] || 'Unknown'}`,
    `Route: ${short(f['Current Route'] || 'No route recorded.', 450)}`,
    `Worker: ${f['Default Worker'] || 'Not set'}`,
    `Phone: ${short(f['Phone Control Rule'] || 'Use approved Slack /ai commands.', 260)}`
  ];
  if (related.length) {
    out.push('Jobs:');
    for (const j of related) out.push(`• ${j.fields?.['Job ID'] || j.id}: ${j.fields?.Status || 'Unknown'} — ${short(j.fields?.['Next Action'] || j.fields?.['Current Step'] || '', 140)}`);
  }
  return out.join('\n');
}

function leaseUntil() {
  return new Date(Date.now() + CFG.leaseMinutes * 60 * 1000).toISOString();
}

async function claimLeaseForJob(record) {
  const f = record.fields || {};
  const jobId = f['Job ID'] || record.id;
  const until = leaseUntil();
  await createRecord(CFG.leasesTable, {
    Lease: `${jobId}-${CFG.workerName}-${Date.now()}`,
    'Job ID': jobId,
    Resource: f.Branch || f['PR URL'] || jobId,
    'Locked By': CFG.workerName,
    'Locked Until': until,
    Status: 'Active',
    'Recovery Action': 'If expired, return job to Needs Worker or Needs Review based on PR state.',
    'Last Heartbeat': new Date().toISOString()
  });
  await updateRecord(CFG.jobsTable, record.id, {
    Status: 'In Progress',
    'Lease Owner': CFG.workerName,
    'Locked Until': until,
    'Last Heartbeat': new Date().toISOString(),
    'Current Step': 'Slack CODE command accepted. Timed lease claimed by durable gateway. Worker execution remains bounded by ticket rules.'
  });
  return `${jobId} leased to ${CFG.workerName} until ${until}.`;
}

async function recordApproval(record, command) {
  const f = record?.fields || {};
  const jobId = f['Job ID'] || 'UNKNOWN-JOB';
  await createRecord(CFG.approvalsTable, {
    Approval: `Approval ${jobId} ${new Date().toISOString()}`,
    'Job ID': jobId,
    Type: 'Merge',
    Status: 'Requested',
    'Approved By': CFG.approvedUser,
    'Approval Text': command,
    'PR URL': f['PR URL'] || '',
    Notes: 'Gateway recorded approval. Merge executor must re-fetch PR, verify tests, and merge with expected head SHA protection.'
  });
  return `${jobId} approval recorded. Merge guard still requires PR re-fetch and expected head SHA.`;
}

async function markJob(record, status, step) {
  if (!record) return 'No job to update.';
  const jobId = record.fields?.['Job ID'] || record.id;
  await updateRecord(CFG.jobsTable, record.id, {
    Status: status,
    'Current Step': step,
    'Last Heartbeat': new Date().toISOString()
  });
  return `${jobId}: ${status}.`;
}

async function handleAllowedCommand(command, target = '') {
  const job = await getCurrentJob();
  let result;
  switch (command) {
    case 'STATUS':
      result = await formatProjectStatus(target, job);
      break;
    case 'PLAN':
      result = target ? `PLAN accepted for ${target}. Create draft tickets only; no code execution.` : 'PLAN accepted. Create draft tickets only; no code execution.';
      break;
    case 'APPROVE PLAN':
      result = await markJob(job, 'Ready', 'Slack APPROVE PLAN accepted. Draft tickets may enter the queue.');
      break;
    case 'CODE':
      result = job ? await claimLeaseForJob(job) : 'No job found to lease.';
      break;
    case 'APPROVE':
      result = await recordApproval(job, command);
      break;
    case 'RETRY':
      result = await markJob(job, 'Ready', 'Slack RETRY accepted. Same route only, bounded by attempts.');
      break;
    case 'SWITCH':
      result = await markJob(job, 'Needs Worker', 'Slack SWITCH accepted. Old lease must be released or expired before new worker starts.');
      break;
    case 'RESET':
      result = await markJob(job, 'Needs Worker', 'Slack RESET accepted. Return to rollback point only; no broad delete.');
      break;
    case 'STOP':
      result = await markJob(job, 'Paused', 'Slack STOP accepted. Job paused safely.');
      break;
    case 'DISCUSS':
      result = `DISCUSS packet:\n${await formatProjectStatus(target, job)}`;
      break;
    default:
      result = 'Unknown command.';
  }
  await logChange(command, result);
  return result;
}

async function handlePayload(payload) {
  const { command, target } = parseCommand(payload);
  const user = String(payload.user_id || payload.user || '').trim();
  const channel = String(payload.channel_id || payload.channel || '').trim();
  if (user !== CFG.approvedUser) return { status: 403, text: 'Rejected: not approved user.' };
  if (channel !== CFG.approvedChannel) return { status: 403, text: 'Rejected: not approved channel.' };
  if (!ALLOWED_COMMANDS.has(command)) return { status: 400, text: 'Allowed: STATUS [project], PLAN [project], APPROVE PLAN [project], CODE [project], APPROVE, RETRY, SWITCH, RESET, STOP [project], DISCUSS [project].' };
  saveAudit({ receivedAt: new Date().toISOString(), command, target, user, channel });
  const text = await handleAllowedCommand(command, target);
  return { status: 200, text };
}

const server = http.createServer(async (req, res) => {
  const chunks = [];
  req.on('data', chunk => chunks.push(chunk));
  req.on('end', async () => {
    try {
      const rawBody = Buffer.concat(chunks).toString('utf8');
      if (req.url === '/health') return writeJson(res, 200, { ok: true, service: 'control-plane-v1', ...credentialState() });
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
