import json, tempfile, os
from engine import init_db, upsert_project, upsert_job, bootstrap, emergency_stop, claim_next_job, complete_job, heartbeat_job, expire_old_leases
from slack_actions import mark_top_job, record_approval


def main():
    db = tempfile.NamedTemporaryFile(delete=True).name
    con = init_db(db)
    upsert_project(con, {
        'project_key':'blue-aces-website',
        'project_name':'Blue Aces Website',
        'status':'Active',
        'production_state':'Live',
        'aliases_json':'["blue aces"]',
        'source':'test',
        'raw_json':'{}'
    })
    upsert_job(con, {
        'job_id':'JOB-T1',
        'project_key':'blue-aces-website',
        'status':'Ready',
        'priority':'High',
        'request':'test',
        'current_step':'',
        'next_action':'run',
        'worker':'vps',
        'branch':'b',
        'pr_url':'',
        'risk':'Green',
        'attempts':0,
        'max_attempts':3,
        'locked_by':'',
        'locked_until':'',
        'rollback_point':'',
        'source':'test',
        'raw_json':'{}'
    })
    a = bootstrap(con, 'CONTINUE', 'blue aces')
    assert a['project']['project_key'] == 'blue-aces-website', a
    assert a['top_job']['job_id'] == 'JOB-T1', a
    assert a['next_safe_action'] == 'claim_lease_when_worker_template_ready', a

    claim = claim_next_job(con, worker_key='tester', lease_seconds=60, project_key='blue-aces-website')
    assert claim['claimed'] is True, claim
    row = con.execute("SELECT status,locked_by FROM jobs WHERE job_id='JOB-T1'").fetchone()
    assert row['status'] == 'In Progress' and row['locked_by'] == 'tester', dict(row)

    hb = heartbeat_job(con, 'JOB-T1', worker_key='tester', extend_seconds=60)
    assert hb['ok'] is True, hb
    done = complete_job(con, 'JOB-T1', worker_key='tester')
    assert done['ok'] is True and done['status'] == 'Needs Review', done
    b = bootstrap(con, 'CONTINUE', 'blue aces')
    assert b['top_job']['status'] == 'Needs Review', b

    plan = mark_top_job(con, 'Planning', 'Plan accepted.', 'blue aces')
    assert plan['updated'] is True and plan['status'] == 'Planning', plan
    ready = mark_top_job(con, 'Ready', 'Ready after plan.', 'blue aces')
    assert ready['updated'] is True and ready['status'] == 'Ready', ready
    appr = record_approval(con, 'blue aces', 'Merge', 'tester')
    assert appr['recorded'] is True and appr['job_id'] == 'JOB-T1', appr

    unknown = emergency_stop(con, 'not a real project')
    assert unknown['stopped'] is False, unknown
    c = bootstrap(con, 'CONTINUE', 'blue aces')
    assert c['top_job']['status'] == 'Ready', c

    s = emergency_stop(con, 'blue aces')
    assert s['stopped'] is True, s
    d = bootstrap(con, 'CONTINUE', 'blue aces')
    assert d['top_job']['status'] == 'Paused', d

    print(json.dumps({'ok': True, 'tests': 13}, sort_keys=True))

if __name__ == '__main__':
    main()
