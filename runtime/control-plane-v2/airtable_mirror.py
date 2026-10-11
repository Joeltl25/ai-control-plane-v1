import argparse, json, os, sqlite3, time, urllib.parse, urllib.request
from engine import DB_PATH, connect, now_iso, event

BASE = os.environ.get('CONTROL_PLANE_AIRTABLE_BASE','apptauaPVwi4qo1EL')
PROJECTS_TABLE = os.environ.get('CONTROL_PLANE_PROJECTS_TABLE','tblMJiGB0mS0Rmwjk')
JOBS_TABLE = os.environ.get('CONTROL_PLANE_JOBS_TABLE','tbl6wejFI9zqisxL3')
PAT_FILE = os.environ.get('AIRTABLE_PAT_FILE','/home/aioperator/.config/ops-dashboard-credentials/AIRTABLE_PAT')

PROJECT_FIELD = 'fld1kCo82XoO41i3a'
PROJECT_STATUS = 'fldqy5pBjgtsZNQ2w'
PROJECT_DESC = 'fldeeIQCakYyZ2oRw'
PROJECT_REPO = 'fldWQUHytGKPiKZ4f'
PROJECT_VPS = 'fldu10dvFZxoRCHLm'
PROJECT_SLACK = 'fld5kjLLLaUA5jmxj'
PROJECT_PROD = 'fldUV2ZmQ0MMcjHWK'
PROJECT_WORKER = 'fld6lRqgBVqFJ1yUM'
PROJECT_ROUTE = 'fldM1YSyfGzXuyLoZ'
PROJECT_BUDGET = 'fldyh2bs7NlcmxXre'
PROJECT_PHONE = 'fld94tXWbyKoyl13o'
PROJECT_DATE = 'fld3J7ppLSFAbQ1bZ'

JOB_ID = 'fldxhKOen7tUaG7Ho'
JOB_PROJECT = 'fldpnvxALt4AhCBLT'
JOB_STATUS = 'fldWnhaJI5MuHGTBL'
JOB_PRIORITY = 'fldUaB3CRJCpPpwcU'
JOB_REQUEST = 'fldokAq2TaJtpo6Hu'
JOB_CURRENT = 'fldPhpxi4e5wPggnc'
JOB_WORKER = 'fldkaKiySqDouG2GT'
JOB_BRANCH = 'fldY7CMqZUMScMqR2'
JOB_ATTEMPTS = 'fldRKhpQoH7P2cXdx'
JOB_MAX_ATTEMPTS = 'fldNZj3N0gR7M84hV'
JOB_PR = 'fldtOc1soh8JKtBgx'
JOB_RISK = 'fldj7try3c3PC34fW'
JOB_NEXT = 'fld7Vfg9fXc335ztg'
JOB_ROLLBACK = 'fld7VWrKs3WhwpaZ3'
JOB_DATE = 'fld96Au8ZPqSRT6aP'


def token():
    with open(PAT_FILE, 'r', encoding='utf-8') as f:
        return f.read().strip()


def airtable_request(method, table, body=None, params=None, record_id=None):
    params = dict(params or {})
    if method in ('POST','PATCH'):
        params['typecast'] = 'true'
    suffix = '/' + record_id if record_id else ''
    url = f'https://api.airtable.com/v0/{BASE}/{table}{suffix}'
    if params:
        url += '?' + urllib.parse.urlencode(params)
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + token(),
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode('utf-8'))


def formula_eq(field_name, value):
    safe = str(value or '').replace('\\', '\\\\').replace('"', '\\"')
    return f'{{{field_name}}}="{safe}"'


def find_record(table, field_name, value):
    data = airtable_request('GET', table, params={'maxRecords':'1', 'filterByFormula': formula_eq(field_name, value)})
    records = data.get('records') or []
    return records[0]['id'] if records else None


def upsert_record(table, merge_field_name, merge_value, fields):
    rid = find_record(table, merge_field_name, merge_value)
    if rid:
        return airtable_request('PATCH', table, {'fields': fields}, record_id=rid)
    return airtable_request('POST', table, {'records': [{'fields': fields}]})


def project_name_for(con, project_key):
    if not project_key:
        return ''
    row = con.execute('SELECT project_name FROM projects WHERE project_key=?', (project_key,)).fetchone()
    return row['project_name'] if row else project_key


def map_project(payload):
    name = payload.get('project_name') or payload.get('project_key')
    fields = {
        PROJECT_FIELD: name,
        PROJECT_STATUS: payload.get('status') or 'Planning',
        PROJECT_PROD: payload.get('production_state') or 'Unknown',
        PROJECT_REPO: payload.get('repo') or '',
        PROJECT_VPS: payload.get('vps_path') or '',
        PROJECT_SLACK: payload.get('slack_channel') or '',
        PROJECT_WORKER: payload.get('default_worker') or '',
        PROJECT_ROUTE: payload.get('current_route') or '',
        PROJECT_BUDGET: payload.get('budget_rule') or '',
        PROJECT_PHONE: payload.get('phone_rule') or '',
        PROJECT_DATE: (payload.get('updated_at') or now_iso())[:10],
    }
    if payload.get('raw_json'):
        fields[PROJECT_DESC] = 'Mirrored from V2 SQLite engine. Source: ' + (payload.get('source') or 'local')
    return PROJECTS_TABLE, 'Project', name, {k:v for k,v in fields.items() if v is not None}


def map_job(con, payload):
    jid = payload.get('job_id') or payload.get('entity_key')
    fields = {JOB_ID: jid}
    if payload.get('project_key'):
        fields[JOB_PROJECT] = project_name_for(con, payload.get('project_key'))
    if 'status' in payload:
        fields[JOB_STATUS] = payload.get('status') or 'Ready'
    if 'priority' in payload:
        fields[JOB_PRIORITY] = payload.get('priority') or 'Normal'
    if 'request' in payload:
        fields[JOB_REQUEST] = payload.get('request') or ''
    if 'current_step' in payload:
        fields[JOB_CURRENT] = payload.get('current_step') or ''
    if 'worker' in payload:
        fields[JOB_WORKER] = payload.get('worker') or ''
    if 'branch' in payload:
        fields[JOB_BRANCH] = payload.get('branch') or ''
    if 'attempts' in payload:
        fields[JOB_ATTEMPTS] = int(payload.get('attempts') or 0)
    if 'max_attempts' in payload:
        fields[JOB_MAX_ATTEMPTS] = int(payload.get('max_attempts') or 3)
    if 'pr_url' in payload:
        fields[JOB_PR] = payload.get('pr_url') or ''
    if 'risk' in payload:
        fields[JOB_RISK] = payload.get('risk') or 'Green'
    if 'next_action' in payload:
        fields[JOB_NEXT] = payload.get('next_action') or ''
    if 'rollback_point' in payload:
        fields[JOB_ROLLBACK] = payload.get('rollback_point') or ''
    if 'updated_at' in payload:
        fields[JOB_DATE] = payload.get('updated_at') or now_iso()
    return JOBS_TABLE, 'Job ID', jid, {k:v for k,v in fields.items() if v is not None}


def map_payload(con, row):
    payload = json.loads(row['payload_json'])
    payload.setdefault('entity_key', row['entity_key'])
    if row['entity'] == 'project':
        return map_project(payload)
    if row['entity'] == 'job':
        return map_job(con, payload)
    return None


def drain(limit=25, apply=False):
    con = connect()
    rows = con.execute("SELECT * FROM sync_outbox WHERE status IN ('Pending','Retry') AND attempts < 8 ORDER BY id LIMIT ?", (limit,)).fetchall()
    results = []
    for row in rows:
        try:
            mapped = map_payload(con, row)
            if not mapped:
                con.execute("UPDATE sync_outbox SET status='Skipped', attempts=attempts+1, last_error=? WHERE id=?", ('No Airtable mapping for entity ' + row['entity'], row['id']))
                results.append({'id': row['id'], 'status': 'skipped'})
                continue
            table, merge_field, merge_value, fields = mapped
            if apply:
                upsert_record(table, merge_field, merge_value, fields)
                status = 'Synced'
                con.execute("UPDATE sync_outbox SET status=?, attempts=attempts+1, last_error=NULL WHERE id=?", (status, row['id']))
            else:
                status = 'DryRun'
            results.append({'id': row['id'], 'entity': row['entity'], 'key': row['entity_key'], 'status': status})
            time.sleep(0.25)
        except Exception as exc:
            con.execute("UPDATE sync_outbox SET status='Retry', attempts=attempts+1, last_error=? WHERE id=?", (str(exc)[:900], row['id']))
            results.append({'id': row['id'], 'status': 'retry', 'error': str(exc)[:160]})
    event(con, 'INFO', 'airtable_mirror', 'Airtable mirror drain completed', data={'apply': apply, 'results': results[:20]})
    return {'ok': True, 'apply': apply, 'processed': len(results), 'results': results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--limit', type=int, default=25)
    args = ap.parse_args()
    print(json.dumps(drain(limit=args.limit, apply=args.apply), sort_keys=True))

if __name__ == '__main__':
    main()
