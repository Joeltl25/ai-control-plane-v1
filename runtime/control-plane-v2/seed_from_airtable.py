import json
import os
import urllib.parse
import urllib.request
from engine import init_db, upsert_project, upsert_job, event, norm

BASE = os.environ.get('CONTROL_PLANE_AIRTABLE_BASE', 'apptauaPVwi4qo1EL')
PROJECTS = os.environ.get('CONTROL_PLANE_PROJECTS_TABLE', 'tblMJiGB0mS0Rmwjk')
JOBS = os.environ.get('CONTROL_PLANE_JOBS_TABLE', 'tbl6wejFI9zqisxL3')
PAT_FILE = os.environ.get('AIRTABLE_PAT_FILE', '/home/aioperator/.config/ops-dashboard-credentials/AIRTABLE_PAT')


def token():
    with open(PAT_FILE, 'r', encoding='utf-8') as f:
        return f.read().strip()


def fetch_records(table):
    out, offset = [], None
    while True:
        params = {'pageSize': '100'}
        if offset:
            params['offset'] = offset
        url = f'https://api.airtable.com/v0/{BASE}/{table}?{urllib.parse.urlencode(params)}'
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token()})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        out.extend(data.get('records', []))
        offset = data.get('offset')
        if not offset:
            break
    return out


def as_text(value):
    if isinstance(value, list):
        return ', '.join(str(x) for x in value)
    if isinstance(value, dict):
        return value.get('name') or json.dumps(value)
    return '' if value is None else str(value)


def project_payload(rec):
    f = rec.get('fields', {})
    name = f.get('Project') or rec['id']
    return {
        'project_key': norm(name),
        'project_name': as_text(name),
        'status': as_text(f.get('Status') or 'Planning'),
        'production_state': as_text(f.get('Production State') or 'Unknown'),
        'repo': as_text(f.get('GitHub Repo') or f.get('Repo URL') or ''),
        'vps_path': as_text(f.get('VPS Path') or ''),
        'slack_channel': as_text(f.get('Slack Channel') or ''),
        'default_worker': as_text(f.get('Default Worker') or ''),
        'current_route': as_text(f.get('Current Route') or ''),
        'phone_rule': as_text(f.get('Phone Control Rule') or ''),
        'budget_rule': as_text(f.get('Budget Rule') or ''),
        'aliases_json': json.dumps([name]),
        'source': 'airtable',
        'raw_json': json.dumps(rec, ensure_ascii=False)
    }


def job_payload(rec):
    f = rec.get('fields', {})
    project_key = norm(f.get('Project Key') or '') if f.get('Project Key') else None
    return {
        'job_id': as_text(f.get('Job ID') or rec['id']),
        'project_key': project_key,
        'status': as_text(f.get('Status') or 'Ready'),
        'priority': as_text(f.get('Priority') or 'Normal'),
        'request': as_text(f.get('Request') or ''),
        'current_step': as_text(f.get('Current Step') or ''),
        'next_action': as_text(f.get('Next Action') or ''),
        'worker': as_text(f.get('Worker') or ''),
        'branch': as_text(f.get('Branch') or ''),
        'pr_url': as_text(f.get('PR URL') or ''),
        'risk': as_text(f.get('Risk') or 'Green'),
        'attempts': int(f.get('Attempts') or 0),
        'max_attempts': int(f.get('Max Attempts') or 3),
        'locked_by': as_text(f.get('Lease Owner') or ''),
        'locked_until': as_text(f.get('Locked Until') or ''),
        'rollback_point': as_text(f.get('Rollback Point') or ''),
        'source': 'airtable',
        'raw_json': json.dumps(rec, ensure_ascii=False)
    }


def main():
    con = init_db()
    project_count = 0
    for rec in fetch_records(PROJECTS):
        upsert_project(con, project_payload(rec))
        project_count += 1
    job_count = 0
    for rec in fetch_records(JOBS):
        upsert_job(con, job_payload(rec))
        job_count += 1
    event(con, 'INFO', 'seed', 'Seeded from Airtable', data={'projects': project_count, 'jobs': job_count})
    print(json.dumps({'ok': True, 'projects': project_count, 'jobs': job_count}, sort_keys=True))


if __name__ == '__main__':
    main()
