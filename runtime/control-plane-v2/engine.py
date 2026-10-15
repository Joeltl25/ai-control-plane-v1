import json, os, sqlite3, time, re, subprocess, uuid
from pathlib import Path

DEFAULT_DB = '/home/aioperator/control-plane/state/control-plane-v2/control-plane.sqlite'
DB_PATH = os.environ.get('CONTROL_PLANE_V2_DB', DEFAULT_DB)

def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def norm(value):
    return re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-') or 'unknown'

def connect(db_path=DB_PATH):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=5, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=5000')
    return con

def init_db(db_path=DB_PATH):
    con = connect(db_path)
    con.executescript('''
    CREATE TABLE IF NOT EXISTS projects (
      project_key TEXT PRIMARY KEY,
      project_name TEXT NOT NULL,
      status TEXT DEFAULT 'Planning',
      production_state TEXT DEFAULT 'Unknown',
      repo TEXT,
      vps_path TEXT,
      slack_channel TEXT,
      default_worker TEXT,
      current_route TEXT,
      phone_rule TEXT,
      budget_rule TEXT,
      aliases_json TEXT DEFAULT '[]',
      source TEXT DEFAULT 'local',
      raw_json TEXT DEFAULT '{}',
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS jobs (
      job_id TEXT PRIMARY KEY,
      project_key TEXT,
      status TEXT NOT NULL,
      priority TEXT DEFAULT 'Normal',
      request TEXT,
      current_step TEXT,
      next_action TEXT,
      worker TEXT,
      branch TEXT,
      pr_url TEXT,
      risk TEXT DEFAULT 'Green',
      attempts INTEGER DEFAULT 0,
      max_attempts INTEGER DEFAULT 3,
      locked_by TEXT,
      locked_until TEXT,
      rollback_point TEXT,
      source TEXT DEFAULT 'local',
      raw_json TEXT DEFAULT '{}',
      updated_at TEXT NOT NULL,
      FOREIGN KEY(project_key) REFERENCES projects(project_key)
    );
    CREATE TABLE IF NOT EXISTS leases (
      lease_id TEXT PRIMARY KEY,
      job_id TEXT NOT NULL,
      resource TEXT,
      locked_by TEXT NOT NULL,
      locked_until TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'Active',
      heartbeat_at TEXT,
      recovery_action TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY(job_id) REFERENCES jobs(job_id)
    );
    CREATE TABLE IF NOT EXISTS approvals (
      approval_id TEXT PRIMARY KEY,
      job_id TEXT,
      approval_type TEXT,
      status TEXT,
      approved_by TEXT,
      approval_text TEXT,
      pr_url TEXT,
      expires_at TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS workers (
      worker_key TEXT PRIMARY KEY,
      status TEXT DEFAULT 'Available',
      role TEXT,
      use_for TEXT,
      do_not_use_for TEXT,
      cost_guard TEXT,
      fallback_worker TEXT,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS budgets (
      budget_key TEXT PRIMARY KEY,
      status TEXT DEFAULT 'Active',
      daily_cap_cents INTEGER DEFAULT 0,
      used_cents INTEGER DEFAULT 0,
      notes TEXT,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
      event_id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      level TEXT NOT NULL,
      source TEXT NOT NULL,
      project_key TEXT,
      job_id TEXT,
      message TEXT NOT NULL,
      data_json TEXT DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS sync_outbox (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      target TEXT NOT NULL,
      op TEXT NOT NULL,
      entity TEXT NOT NULL,
      entity_key TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'Pending',
      attempts INTEGER NOT NULL DEFAULT 0,
      last_error TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_status_priority ON jobs(status, priority, updated_at);
    CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_key);
    CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, ts);
    CREATE INDEX IF NOT EXISTS idx_sync_pending ON sync_outbox(status, attempts, ts);
    ''')
    return con

def enqueue_outbox(con, entity, key, payload, target='airtable', op='upsert'):
    con.execute('INSERT INTO sync_outbox(ts,target,op,entity,entity_key,payload_json) VALUES(?,?,?,?,?,?)',
                (now_iso(), target, op, entity, key, json.dumps(payload, ensure_ascii=False)))

def event(con, level, source, message, project_key=None, job_id=None, data=None):
    con.execute('INSERT INTO events(ts,level,source,project_key,job_id,message,data_json) VALUES(?,?,?,?,?,?,?)',
                (now_iso(), level, source, project_key, job_id, message, json.dumps(data or {}, ensure_ascii=False)))

def upsert_project(con, p):
    key = p.get('project_key') or norm(p.get('project_name'))
    defaults = {
        'project_name': key, 'status': 'Planning', 'production_state': 'Unknown',
        'repo': '', 'vps_path': '', 'slack_channel': '', 'default_worker': '',
        'current_route': '', 'phone_rule': '', 'budget_rule': '',
        'aliases_json': '[]', 'source': 'local', 'raw_json': '{}'
    }
    defaults.update(p)
    payload = dict(defaults, project_key=key, updated_at=now_iso())
    con.execute('''INSERT INTO projects(project_key,project_name,status,production_state,repo,vps_path,slack_channel,default_worker,current_route,phone_rule,budget_rule,aliases_json,source,raw_json,updated_at)
      VALUES(:project_key,:project_name,:status,:production_state,:repo,:vps_path,:slack_channel,:default_worker,:current_route,:phone_rule,:budget_rule,:aliases_json,:source,:raw_json,:updated_at)
      ON CONFLICT(project_key) DO UPDATE SET project_name=excluded.project_name,status=excluded.status,production_state=excluded.production_state,repo=excluded.repo,vps_path=excluded.vps_path,slack_channel=excluded.slack_channel,default_worker=excluded.default_worker,current_route=excluded.current_route,phone_rule=excluded.phone_rule,budget_rule=excluded.budget_rule,aliases_json=excluded.aliases_json,source=excluded.source,raw_json=excluded.raw_json,updated_at=excluded.updated_at''', payload)
    enqueue_outbox(con, 'project', key, payload)
    return key

def upsert_job(con, j):
    key = j.get('job_id') or f"job-{int(time.time())}"
    defaults = {
        'project_key': None, 'status': 'Ready', 'priority': 'Normal',
        'request': '', 'current_step': '', 'next_action': '', 'worker': '',
        'branch': '', 'pr_url': '', 'risk': 'Green', 'attempts': 0,
        'max_attempts': 3, 'locked_by': '', 'locked_until': '',
        'rollback_point': '', 'source': 'local', 'raw_json': '{}'
    }
    defaults.update(j)
    payload = dict(defaults, job_id=key, updated_at=now_iso())
    con.execute('''INSERT INTO jobs(job_id,project_key,status,priority,request,current_step,next_action,worker,branch,pr_url,risk,attempts,max_attempts,locked_by,locked_until,rollback_point,source,raw_json,updated_at)
      VALUES(:job_id,:project_key,:status,:priority,:request,:current_step,:next_action,:worker,:branch,:pr_url,:risk,:attempts,:max_attempts,:locked_by,:locked_until,:rollback_point,:source,:raw_json,:updated_at)
      ON CONFLICT(job_id) DO UPDATE SET project_key=excluded.project_key,status=excluded.status,priority=excluded.priority,request=excluded.request,current_step=excluded.current_step,next_action=excluded.next_action,worker=excluded.worker,branch=excluded.branch,pr_url=excluded.pr_url,risk=excluded.risk,attempts=excluded.attempts,max_attempts=excluded.max_attempts,locked_by=excluded.locked_by,locked_until=excluded.locked_until,rollback_point=excluded.rollback_point,source=excluded.source,raw_json=excluded.raw_json,updated_at=excluded.updated_at''', payload)
    enqueue_outbox(con, 'job', key, payload)
    return key

def rows_to_dicts(rows):
    return [dict(r) for r in rows]

def status_rank(status):
    order = {
      'Needs Review': 0, 'Blocked': 1, 'Needs Credential': 2,
      'In Progress': 3, 'Ready': 4, 'Needs Worker': 5,
      'Paused': 8, 'Done': 9, 'Archived': 10
    }
    return order.get(status or '', 6)

def priority_rank(priority):
    return {'Urgent':0, 'High':1, 'Normal':2, 'Low':3}.get(priority or '', 2)

def list_projects(con):
    return rows_to_dicts(con.execute("SELECT * FROM projects WHERE status != 'Archived' ORDER BY project_name LIMIT 50"))

def find_project(con, target):
    needle = norm(target)
    if not needle:
        return None
    rows = list_projects(con)
    def name_match(row):
        aliases = json.loads(row.get('aliases_json') or '[]')
        names = [row['project_key'] if 'project_key' in row.keys() else None, row.get('project_name')] + aliases
        return any(needle == norm(x) or needle in norm(x) or norm(x) in needle for x in names if x)
    primary = next((r for r in rows if name_match(r)), None)
    if primary:
        return primary
    def secondary_match(row):
        fields = [row.get('repo'), row.get('current_route'), row.get('phone_rule')]
        return any(needle == norm(x) or needle in norm(x) for x in fields if x)
    return next((r for r in rows if secondary_match(r)), None)

def top_job(con, project_key=None):
    rows = rows_to_dicts(con.execute('SELECT * FROM jobs WHERE status NOT IN (\'Done\',\'Archived\') ORDER BY updated_at DESC LIMIT 100'))
    if project_key:
        rows = [r for r in rows if r.get('project_key') == project_key]
    if not rows:
        return None
    rows.sort(key=lambda r: (status_rank(r.get('status')), priority_rank(r.get('priority')), r.get('updated_at') or ''))
    return rows[0]

def counts(con):
    names = ['projects','jobs','leases','events','sync_outbox']
    return {name: con.execute(f'SELECT COUNT(*) c FROM {name}').fetchone()['c'] for name in names}

def bootstrap(con, intent='CONTINUE', target=''):
    project = find_project(con, target) if target else None
    job = top_job(con, project.get('project_key') if project else None)
    active_projects = list_projects(con)
    result = {
      'ok': True,
      'version': 'v2-prototype',
      'intent': intent or 'CONTINUE',
      'target': target or '',
      'project': project,
      'top_job': job,
      'counts': counts(con),
      'projects': active_projects[:12],
      'next_safe_action': 'status_only'
    }
    if job:
        if job.get('status') in ('Needs Review','Blocked','Needs Credential'):
            result['next_safe_action'] = 'ask_user_or_alert_slack'
        elif job.get('status') in ('Ready','Needs Worker'):
            result['next_safe_action'] = 'claim_lease_when_worker_template_ready'
        elif job.get('status') == 'In Progress':
            result['next_safe_action'] = 'monitor_active_lease'
    event(con, 'INFO', 'bootstrap', 'bootstrap_or_continue called', project.get('project_key') if project else None, job.get('job_id') if job else None, {'intent': intent, 'target': target, 'next': result['next_safe_action']})
    return result

def safe_systemd_instance(value):
    raw = str(value or 'unknown')
    safe = re.sub(r'[^A-Za-z0-9_.:-]+', '-', raw).strip('-')[:80]
    return safe or 'unknown'

def worker_unit_for_job(job):
    return 'control-plane-v2-worker@' + safe_systemd_instance(job.get('job_id')) + '.service'

def systemd_user_env():
    uid = str(os.getuid())
    env = os.environ.copy()
    env.setdefault('XDG_RUNTIME_DIR', '/run/user/' + uid)
    env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/' + uid + '/bus')
    return env

def run_systemctl(args, timeout=8):
    try:
        cp = subprocess.run(['systemctl','--user'] + list(args), text=True, capture_output=True, timeout=timeout, env=systemd_user_env())
        return {'ok': cp.returncode == 0, 'returncode': cp.returncode, 'stdout': cp.stdout.strip()[:500], 'stderr': cp.stderr.strip()[:500]}
    except Exception as exc:
        return {'ok': False, 'returncode': -1, 'stdout': '', 'stderr': str(exc)[:500]}

def kill_worker_service_for_job(job):
    unit = worker_unit_for_job(job)
    if not unit.startswith('control-plane-v2-worker@') or not unit.endswith('.service'):
        return {'unit': unit, 'killed': False, 'error': 'unsafe_unit_name'}
    show = run_systemctl(['show', unit, '--property=LoadState', '--property=ActiveState', '--value'])
    states = [x for x in (show.get('stdout') or '').splitlines() if x]
    load_state = states[0] if len(states) > 0 else 'unknown'
    active_state = states[1] if len(states) > 1 else 'unknown'
    result = {'unit': unit, 'load_state': load_state, 'active_state': active_state, 'killed': False, 'show': show}
    if active_state not in ('active', 'activating'):
        result['message'] = 'worker unit not active; no OS kill needed'
        return result
    result['term'] = run_systemctl(['kill', '-s', 'SIGTERM', unit])
    time.sleep(1)
    result['kill'] = run_systemctl(['kill', '-s', 'SIGKILL', unit])
    result['stop'] = run_systemctl(['stop', unit])
    result['killed'] = bool(result['term'].get('ok') or result['kill'].get('ok') or result['stop'].get('ok'))
    return result


def utc_plus_seconds(seconds):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + int(seconds)))

def expire_old_leases(con):
    ts = now_iso()
    expired = con.execute("SELECT lease_id, job_id FROM leases WHERE status='Active' AND locked_until < ?", (ts,)).fetchall()
    for row in expired:
        con.execute("UPDATE leases SET status='Expired', recovery_action='Lease expired by queue manager', heartbeat_at=? WHERE lease_id=?", (ts, row['lease_id']))
        con.execute("UPDATE jobs SET status='Needs Worker', locked_by=NULL, locked_until=NULL, current_step=?, updated_at=? WHERE job_id=? AND status='In Progress'", ('Lease expired; returned to Needs Worker.', ts, row['job_id']))
        enqueue_outbox(con, 'job', row['job_id'], {'job_id': row['job_id'], 'status': 'Needs Worker', 'current_step': 'Lease expired; returned to Needs Worker.', 'updated_at': ts})
    if expired:
        event(con, 'WARN', 'lease', 'Expired stale leases', data={'count': len(expired), 'jobs': [r['job_id'] for r in expired]})
    return len(expired)

def claim_next_job(con, worker_key='v2-local-worker', lease_seconds=1200, project_key=None, job_id=None):
    expire_old_leases(con)
    if job_id:
        rows = rows_to_dicts(con.execute("SELECT * FROM jobs WHERE job_id=? AND status IN ('Ready','Needs Worker') LIMIT 1", (job_id,)))
    else:
        rows = rows_to_dicts(con.execute("SELECT * FROM jobs WHERE status IN ('Ready','Needs Worker') ORDER BY updated_at DESC LIMIT 200"))
    if project_key:
        rows = [r for r in rows if r.get('project_key') == project_key]
    if not rows:
        event(con, 'INFO', 'claim', 'No claimable job found', project_key=project_key, data={'worker': worker_key})
        return {'ok': True, 'claimed': False, 'message': 'No claimable job found.'}
    rows.sort(key=lambda r: (priority_rank(r.get('priority')), r.get('updated_at') or ''))
    job = rows[0]
    if int(job.get('attempts') or 0) >= int(job.get('max_attempts') or 0):
        return {'ok':False,'claimed':False,'reason':'attempt_limit_reached'}
    lease_id = 'lease-' + uuid.uuid4().hex[:16]
    locked_until = utc_plus_seconds(lease_seconds)
    now = now_iso()
    try:
        con.execute('BEGIN IMMEDIATE')
        fresh = con.execute("SELECT status, locked_by FROM jobs WHERE job_id=?", (job['job_id'],)).fetchone()
        if not fresh or fresh['status'] not in ('Ready','Needs Worker') or fresh['locked_by']:
            con.execute('ROLLBACK')
            return {'ok': True, 'claimed': False, 'message': 'Job changed before claim.'}
        con.execute("INSERT INTO leases(lease_id,job_id,resource,locked_by,locked_until,status,heartbeat_at,created_at) VALUES(?,?,?,?,?,'Active',?,?)",
                    (lease_id, job['job_id'], 'systemd-worker', worker_key, locked_until, now, now))
        con.execute("UPDATE jobs SET status='In Progress', attempts=attempts+1, locked_by=?, locked_until=?, current_step=?, updated_at=? WHERE job_id=?",
                    (worker_key, locked_until, 'Claimed by V2 queue manager; placeholder worker execution allowed only.', now, job['job_id']))
        con.execute('COMMIT')
    except Exception:
        try:
            con.execute('ROLLBACK')
        except Exception:
            pass
        raise
    payload = {'job_id': job['job_id'], 'status': 'In Progress', 'locked_by': worker_key, 'locked_until': locked_until, 'current_step': 'Claimed by V2 queue manager; placeholder worker execution allowed only.', 'updated_at': now}
    enqueue_outbox(con, 'job', job['job_id'], payload)
    event(con, 'INFO', 'claim', 'Claimed job lease', job.get('project_key'), job.get('job_id'), {'lease_id': lease_id, 'worker': worker_key, 'locked_until': locked_until})
    return {'ok': True, 'claimed': True, 'job_id': job['job_id'], 'lease_id': lease_id, 'locked_until': locked_until, 'worker': worker_key}

def complete_job(con, job_id, worker_key='v2-local-worker', status='Needs Review', message='Placeholder worker completed; needs review before real execution.'):
    now = now_iso()
    row = con.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
    if not row:
        return {'ok': False, 'error': 'job_not_found'}
    if row['locked_by'] and row['locked_by'] != worker_key:
        return {'ok': False, 'error': 'lease_owned_by_other_worker', 'locked_by': row['locked_by']}
    con.execute("UPDATE leases SET status='Released', heartbeat_at=?, recovery_action=? WHERE job_id=? AND status='Active'", (now, 'Completed by worker manager', job_id))
    con.execute("UPDATE jobs SET status=?, current_step=?, locked_by=NULL, locked_until=NULL, updated_at=? WHERE job_id=?", (status, message, now, job_id))
    enqueue_outbox(con, 'job', job_id, {'job_id': job_id, 'status': status, 'current_step': message, 'updated_at': now})
    event(con, 'INFO', 'complete', 'Completed job lease', row['project_key'] if 'project_key' in row.keys() else None, job_id, {'status': status, 'worker': worker_key})
    return {'ok': True, 'job_id': job_id, 'status': status}

def heartbeat_job(con, job_id, worker_key='v2-local-worker', extend_seconds=1200):
    now = now_iso()
    locked_until = utc_plus_seconds(extend_seconds)
    row = con.execute("SELECT * FROM jobs WHERE job_id=? AND locked_by=?", (job_id, worker_key)).fetchone()
    if not row:
        return {'ok': False, 'error': 'active_job_not_found_for_worker'}
    con.execute("UPDATE leases SET heartbeat_at=?, locked_until=? WHERE job_id=? AND locked_by=? AND status='Active'", (now, locked_until, job_id, worker_key))
    con.execute("UPDATE jobs SET locked_until=?, updated_at=? WHERE job_id=?", (locked_until, now, job_id))
    event(con, 'INFO', 'heartbeat', 'Worker heartbeat', row['project_key'] if 'project_key' in row.keys() else None, job_id, {'worker': worker_key, 'locked_until': locked_until})
    return {'ok': True, 'job_id': job_id, 'locked_until': locked_until}

def emergency_stop(con, target=''):
    target_text = str(target or '').strip()
    project = None
    project_key = None
    if target_text and norm(target_text) not in ('all', 'global'):
        project = find_project(con, target_text)
        if not project:
            event(con, 'WARN', 'stop', 'STOP requested for unknown target; no global fallback used', None, None, {'target': target_text})
            return {'ok': True, 'stopped': False, 'message': 'Unknown target; no worker stopped.'}
        project_key = project.get('project_key')
    job = top_job(con, project_key)
    if not job:
        event(con, 'WARN', 'stop', 'STOP requested but no active job found', project_key)
        return {'ok': True, 'stopped': False, 'message': 'No active job found.'}
    kill_result = kill_worker_service_for_job(job)
    con.execute("UPDATE jobs SET status='Paused', current_step=?, locked_by=NULL, locked_until=NULL, updated_at=? WHERE job_id=?",
                ('Emergency STOP requested. Exact worker service kill attempted: ' + kill_result.get('unit','unknown'), now_iso(), job['job_id']))
    con.execute("UPDATE leases SET status='Expired', recovery_action='Emergency STOP released lease', heartbeat_at=? WHERE job_id=? AND status='Active'",
                (now_iso(), job['job_id']))
    event(con, 'WARN', 'stop', 'Emergency STOP recorded and worker kill hook attempted', job.get('project_key'), job.get('job_id'), kill_result)
    enqueue_outbox(con, 'job', job['job_id'], {'job_id': job['job_id'], 'status': 'Paused', 'reason': 'Emergency STOP', 'kill_result': kill_result, 'updated_at': now_iso()})
    return {'ok': True, 'stopped': True, 'job_id': job['job_id'], 'kill_result': kill_result, 'message': 'STOP recorded; exact worker service kill attempted; active lease released.'}


def mark_top_job(con, status, step, target=''):
    project = find_project(con, target) if target else None
    project_key = project.get('project_key') if project else None
    job = top_job(con, project_key)
    if not job:
        event(con, 'WARN', 'mark', 'No job found to update', project_key, None, {'status': status, 'target': target})
        return {'ok': True, 'updated': False, 'message': 'No job found to update.'}
    now = now_iso()
    con.execute("UPDATE jobs SET status=?, current_step=?, updated_at=? WHERE job_id=?", (status, step, now, job['job_id']))
    enqueue_outbox(con, 'job', job['job_id'], {'job_id': job['job_id'], 'status': status, 'current_step': step, 'updated_at': now})
    event(con, 'INFO', 'mark', 'Job status changed by command', job.get('project_key'), job.get('job_id'), {'status': status, 'step': step, 'target': target})
    return {'ok': True, 'updated': True, 'job_id': job['job_id'], 'status': status, 'message': f"{job['job_id']}: {status}."}

def record_approval(con, target='', approval_type='Merge', approved_by=''):
    project = find_project(con, target) if target else None
    project_key = project.get('project_key') if project else None
    job = top_job(con, project_key)
    if not job:
        event(con, 'WARN', 'approval', 'No job found for approval', project_key, None, {'target': target})
        return {'ok': True, 'recorded': False, 'message': 'No job found for approval.'}
    now = now_iso()
    approval_id = 'approval-' + uuid.uuid4().hex[:16]
    con.execute("INSERT INTO approvals(approval_id,job_id,approval_type,status,approved_by,approval_text,pr_url,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (approval_id, job['job_id'], approval_type, 'Requested', approved_by, 'Approval recorded by V2 Slack/launcher route', job.get('pr_url') or '', now))
    event(con, 'INFO', 'approval', 'Approval recorded', job.get('project_key'), job.get('job_id'), {'approval_id': approval_id, 'type': approval_type, 'approved_by': approved_by})
    return {'ok': True, 'recorded': True, 'approval_id': approval_id, 'job_id': job['job_id'], 'message': f"{job['job_id']} approval recorded. Merge guard still requires PR re-fetch and expected head SHA."}
