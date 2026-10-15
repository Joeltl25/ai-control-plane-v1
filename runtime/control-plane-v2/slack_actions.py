import uuid
from engine import find_project, top_job, now_iso, enqueue_outbox, event


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


def approve_exact_job(con,target='',approved_by=''):
    """Trigger one reviewed PR merge guard, never accept a vague or wrong PR."""
    from merge_guard import approve
    from github_publisher import token_status
    from engine import run_systemctl,safe_systemd_instance
    from watchdog import safe_file_status,SLACK_WEBHOOK_URL_FILE
    ok,reason=token_status()
    if not ok:return {'recorded':False,'message':'Merge unavailable: '+reason+'. No PR was merged.'}
    hook=safe_file_status(SLACK_WEBHOOK_URL_FILE)
    from slack_replies import callback_ready
    from merge_guard import approval_candidate
    try:
        candidate=approval_candidate(con,str(target or '').strip())[0]['job_id']
    except (ValueError,KeyError,TypeError):
        return {'recorded':False,'message':'Merge unavailable: no exact approved PR candidate.'}
    if not (hook['present'] and hook['safe_permissions']) and not callback_ready(candidate):
        return {'recorded':False,'message':'Merge unavailable: Slack alert route not configured or expired.'}
    try:
        record=approve(con,str(target or '').strip(),approved_by)
    except (ValueError,KeyError,TypeError) as exc:
        return {'recorded':False,'message':'APPROVE refused: '+str(exc)[:160]}
    unit='control-plane-v2-merge@'+safe_systemd_instance(record['job_id'])+'.service'
    result=run_systemctl(['start','--no-block',unit],timeout=10)
    if not result['ok']:
        event(con,'ERROR','slack_actions','Approved merge service could not start',job_id=record['job_id'],data={'unit':unit})
        return {'recorded':True,'message':'Approval recorded but merge service unavailable. No PR merged.'}
    return {'recorded':True,'message':record['job_id']+' approved. Rebase, tests and merge guard started for '+record['pr_url']}
