import hmac, hashlib, json, os, time, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from engine import init_db, bootstrap, emergency_stop, counts, claim_next_job, complete_job, heartbeat_job
from slack_actions import mark_top_job, record_approval

HOST = os.environ.get('CONTROL_PLANE_V2_HOST','127.0.0.1')
PORT = int(os.environ.get('CONTROL_PLANE_V2_PORT','8799'))
DB = os.environ.get('CONTROL_PLANE_V2_DB','/home/aioperator/control-plane/state/control-plane-v2/control-plane.sqlite')
TOKEN_FILE = os.environ.get('CONTROL_PLANE_V2_API_TOKEN_FILE','/home/aioperator/worker/credentials/ai-control-plane/control-plane-v2-api-token')
SLACK_SIGNING_SECRET_FILE = os.environ.get('SLACK_SIGNING_SECRET_FILE','/home/aioperator/worker/credentials/ai-control-plane/slack-signing-secret')
APPROVED_USER = os.environ.get('CONTROL_PLANE_APPROVED_USER','U0C2MN0DU49')
APPROVED_CHANNEL = os.environ.get('CONTROL_PLANE_APPROVED_CHANNEL','C0C7UTEHR4K')
ALLOWED_COMMANDS = {'STATUS','PLAN','APPROVE PLAN','CODE','APPROVE','RETRY','SWITCH','RESET','STOP','DISCUSS'}

def read_secret(path):
    try:
        return open(path, 'r', encoding='utf-8').read().strip()
    except FileNotFoundError:
        return ''

def short(value, limit=900):
    return ' '.join(str(value or '').split())[:limit]

def slack_json(text, response_type='ephemeral'):
    return {'response_type': response_type, 'text': text}

def parse_command(payload):
    raw = str(payload.get('text') or '').strip().lstrip('/')
    normalized = ' '.join(raw.split())
    if not normalized:
        return 'STATUS', ''
    upper = normalized.upper()
    if upper == 'APPROVE PLAN' or upper.startswith('APPROVE PLAN '):
        return 'APPROVE PLAN', normalized[len('APPROVE PLAN'):].strip()
    parts = normalized.split(' ', 1)
    return parts[0].upper(), (parts[1].strip() if len(parts) > 1 else '')

def parse_body(raw, content_type):
    if 'application/json' in (content_type or ''):
        return json.loads(raw.decode('utf-8') or '{}')
    data = urllib.parse.parse_qs(raw.decode('utf-8'), keep_blank_values=True)
    return {k: v[-1] if v else '' for k, v in data.items()}

def verify_slack(headers, raw):
    secret = read_secret(SLACK_SIGNING_SECRET_FILE)
    if not secret:
        return False, 'SLACK_SIGNING_SECRET_MISSING'
    ts = headers.get('x-slack-request-timestamp') or headers.get('X-Slack-Request-Timestamp') or ''
    sig = headers.get('x-slack-signature') or headers.get('X-Slack-Signature') or ''
    if not ts or not sig:
        return False, 'SLACK_SIGNATURE_HEADERS_MISSING'
    try:
        age = abs(int(time.time()) - int(ts))
    except Exception:
        return False, 'SLACK_SIGNATURE_TIMESTAMP_INVALID'
    if age > 300:
        return False, 'SLACK_SIGNATURE_TIMESTAMP_INVALID'
    base = b'v0:' + ts.encode('utf-8') + b':' + raw
    expected = 'v0=' + hmac.new(secret.encode('utf-8'), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False, 'SLACK_SIGNATURE_INVALID'
    return True, ''

def format_bootstrap(result, target=''):
    if not result.get('ok'):
        return 'V2 error: ' + short(result.get('error'), 300)
    project = result.get('project') or {}
    job = result.get('top_job') or {}
    lines = []
    if target and project:
        lines.append(f"{project.get('project_name') or target}: {project.get('status','Unknown')} / {project.get('production_state','Unknown')}")
        lines.append('Route: ' + short(project.get('current_route') or 'No route recorded.', 360))
        lines.append('Worker: ' + short(project.get('default_worker') or 'Not set.', 120))
        lines.append('Phone: ' + short(project.get('phone_rule') or 'Use approved Slack /ai commands.', 220))
    else:
        lines.append('AI control plane V2 status:')
        lines.append('Projects:')
        for p in (result.get('projects') or [])[:12]:
            lines.append(f"• {p.get('project_name')}: {p.get('status','Unknown')} / {p.get('production_state','Unknown')}")
    lines.append('')
    lines.append('Top job:')
    if job:
        lines.append(f"{job.get('job_id')}: {job.get('status')}")
        lines.append('Next: ' + short(job.get('next_action') or job.get('current_step') or 'None recorded.', 260))
        if job.get('pr_url'):
            lines.append('PR: ' + job.get('pr_url'))
    else:
        lines.append('No active job found.')
    lines.append('')
    lines.append('V2 engine: SQLite source of truth; Airtable mirror only.')
    return '\n'.join(lines)

def handle_v2_command(payload):
    command, target = parse_command(payload)
    user = str(payload.get('user_id') or payload.get('user') or '').strip()
    channel = str(payload.get('channel_id') or payload.get('channel') or '').strip()
    if user != APPROVED_USER:
        return 403, 'Rejected: not approved user.'
    if channel != APPROVED_CHANNEL:
        return 403, 'Rejected: not approved channel.'
    if command not in ALLOWED_COMMANDS:
        return 400, 'Allowed: STATUS [project], PLAN [project], APPROVE PLAN [project], CODE [project], APPROVE, RETRY, SWITCH, RESET, STOP [project], DISCUSS [project].'
    con = init_db(DB)
    if command == 'STATUS':
        return 200, format_bootstrap(bootstrap(con, 'STATUS', target), target)
    if command == 'DISCUSS':
        return 200, 'DISCUSS packet:\n' + format_bootstrap(bootstrap(con, 'DISCUSS', target), target)
    if command == 'PLAN':
        r = mark_top_job(con, 'Planning', 'Slack PLAN accepted. Create draft tickets only; no code execution.', target)
        return 200, r.get('message') + (' Target: ' + target if target else '')
    if command == 'APPROVE PLAN':
        r = mark_top_job(con, 'Ready', 'Slack APPROVE PLAN accepted. Draft tickets may enter the V2 queue.', target)
        return 200, r.get('message')
    if command == 'CODE':
        from queue_manager import run_once
        r = run_once(apply=True)
        if r.get('claimed'):
            return 200, f"{r['job_id']} accepted by bounded worker. Review progress in Slack."
        return 200, 'CODE not started: ' + str(r.get('reason') or r.get('message') or 'NO_ELIGIBLE_JOB')
    if command == 'APPROVE':
        r = record_approval(con, target, 'Merge', user)
        return 200, r.get('message')
    if command == 'RETRY':
        r = mark_top_job(con, 'Ready', 'Slack RETRY accepted. Same route only, bounded by attempts.', target)
        return 200, r.get('message')
    if command == 'SWITCH':
        r = mark_top_job(con, 'Needs Worker', 'Slack SWITCH accepted. Old lease must be released or expired before new worker starts.', target)
        return 200, r.get('message')
    if command == 'RESET':
        r = mark_top_job(con, 'Needs Worker', 'Slack RESET accepted. Return to rollback point only; no broad delete.', target)
        return 200, r.get('message')
    if command == 'STOP':
        r = emergency_stop(con, target)
        return 200, r.get('message')
    return 400, 'Unknown command.'

class Handler(BaseHTTPRequestHandler):
    server_version = 'control-plane-v2/0.2'
    def log_message(self, fmt, *args):
        print(json.dumps({'event':'request','client':self.client_address[0],'method':self.command,'path':self.path,'message':fmt % args}))
    def write(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('content-type','application/json; charset=utf-8')
        self.send_header('cache-control','no-store')
        self.send_header('content-length', str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def read_raw(self):
        n = int(self.headers.get('content-length') or 0)
        return self.rfile.read(n) if n else b''
    def read_json(self):
        raw = self.read_raw()
        if not raw: return {}
        return json.loads(raw.decode('utf-8'))
    def auth_ok(self):
        expected = read_secret(TOKEN_FILE)
        got = self.headers.get('authorization') or ''
        if not expected or not got.lower().startswith('bearer '):
            return False
        return hmac.compare_digest(got[7:].strip(), expected)

    def do_GET(self):
        if self.path != '/health': return self.write(404, {'ok':False,'error':'not_found'})
        con = init_db(DB)
        return self.write(200, {'ok':True,'service':'control-plane-v2','db':DB,'counts':counts(con)})
    def do_POST(self):
        try:
            if self.path == '/slack/command':
                raw = self.read_raw()
                ok, err = verify_slack(self.headers, raw)
                if not ok:
                    return self.write(401, slack_json('Rejected: ' + err))
                payload = parse_body(raw, self.headers.get('content-type') or '')
                status, text = handle_v2_command(payload)
                return self.write(status, slack_json(text))
            payload = self.read_json()
            con = init_db(DB)
            if self.path == '/bootstrap_or_continue':
                if not self.auth_ok():
                    return self.write(401, {'ok':False,'error':'unauthorized'})
                return self.write(200, bootstrap(con, payload.get('intent','CONTINUE'), payload.get('project','') or payload.get('target','')))
            if self.path == '/claim_next':
                if not self.auth_ok():
                    return self.write(401, {'ok':False,'error':'unauthorized'})
                from queue_manager import run_once
                return self.write(200, run_once(apply=True))
            if self.path == '/heartbeat':
                if not self.auth_ok():
                    return self.write(401, {'ok':False,'error':'unauthorized'})
                return self.write(200, heartbeat_job(con, payload.get('job_id',''), payload.get('worker','v2-api-worker'), int(payload.get('extend_seconds') or 1200)))
            if self.path == '/complete':
                if not self.auth_ok():
                    return self.write(401, {'ok':False,'error':'unauthorized'})
                return self.write(409, {'ok':False,'reason':'EXTERNAL_COMPLETION_DISABLED','message':'Only leased worker and publisher units may complete coding jobs.'})
            if self.path == '/stop':
                if not self.auth_ok():
                    return self.write(401, {'ok':False,'error':'unauthorized'})
                return self.write(200, emergency_stop(con, payload.get('project','') or payload.get('target','')))
            return self.write(404, {'ok':False,'error':'not_found'})
        except Exception as e:
            return self.write(500, {'ok':False,'error':str(e)[:500]})

def main():
    init_db(DB)
    print(json.dumps({'event':'control_plane_v2_started','host':HOST,'port':PORT,'db':DB}))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

if __name__ == '__main__':
    main()
