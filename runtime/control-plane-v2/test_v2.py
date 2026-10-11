import json
import tempfile
from engine import init_db, upsert_project, upsert_job, bootstrap, emergency_stop


def main():
    db = tempfile.NamedTemporaryFile(delete=True).name
    con = init_db(db)
    upsert_project(con, {
        'project_key': 'blue-aces-website',
        'project_name': 'Blue Aces Website',
        'status': 'Active',
        'production_state': 'Live',
        'aliases_json': '["blue aces"]',
        'source': 'test',
        'raw_json': '{}'
    })
    upsert_job(con, {
        'job_id': 'JOB-T1',
        'project_key': 'blue-aces-website',
        'status': 'Ready',
        'priority': 'High',
        'request': 'test',
        'next_action': 'run',
        'worker': 'vps',
        'branch': 'b',
        'source': 'test',
        'raw_json': '{}'
    })
    a = bootstrap(con, 'CONTINUE', 'blue aces')
    assert a['project']['project_key'] == 'blue-aces-website', a
    assert a['top_job']['job_id'] == 'JOB-T1', a
    assert a['next_safe_action'] == 'claim_lease_when_worker_template_ready', a
    s = emergency_stop(con, 'blue aces')
    assert s['stopped'] is True, s
    b = bootstrap(con, 'CONTINUE', 'blue aces')
    assert b['top_job']['status'] == 'Paused', b
    print(json.dumps({'ok': True, 'tests': 4}, sort_keys=True))


if __name__ == '__main__':
    main()
