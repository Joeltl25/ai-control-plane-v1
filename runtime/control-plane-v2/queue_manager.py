"""Dispatch only fully validated, approved V1 coding tickets to a systemd worker."""
import json, os, subprocess
from pathlib import Path
from engine import init_db, claim_next_job, complete_job, event, systemd_user_env, safe_systemd_instance
from ticket_validator import validate
from coding_executor import prepare_ticket
from github_publisher import token_status, REPO

WORKER='v2-queue-manager'
PILOT_PROJECT='ai-workspace-control-plane'
SLACK_WEBHOOK=Path('/home/aioperator/worker/credentials/ai-control-plane/slack-webhook-url')

def run_once(apply=False):
    con=init_db()
    if os.environ.get('CONTROL_PLANE_V2_REAL_WORKER_READY')!='1':
        return {'ok':True,'claimed':False,'reason':'REAL_WORKER_NOT_READY'}
    ok,reason=token_status()
    if not ok:return {'ok':True,'claimed':False,'reason':reason}
    from watchdog import safe_file_status
    alert=safe_file_status(SLACK_WEBHOOK)
    if not (alert['present'] and alert['non_empty'] and alert['safe_permissions']) or SLACK_WEBHOOK.is_symlink():
        return {'ok':True,'claimed':False,'reason':'SLACK_WEBHOOK_NOT_CONFIGURED'}
    rows=con.execute("SELECT * FROM jobs WHERE project_key=? AND status IN ('Ready','Needs Worker') ORDER BY updated_at DESC LIMIT 20",(PILOT_PROJECT,)).fetchall()
    for row in rows:
        try:
            ticket=json.loads(row['raw_json'] or '{}').get('coding_ticket')
            prepare_ticket(ticket)
            if ticket['job_id']!=row['job_id'] or (row['branch'] and ticket['branch']!=row['branch']):
                raise ValueError('ticket_does_not_match_job')
            if int(row['attempts'] or 0)>=min(int(row['max_attempts'] or 0),ticket['max_attempts']):
                raise ValueError('attempt_limit_reached')
        except (ValueError,KeyError,TypeError) as exc:
            continue  # Do not claim malformed jobs; fail closed.
        if not apply:return {'ok':True,'claimed':False,'candidate':row['job_id'],'reason':'DRY_RUN'}
        claim=claim_next_job(con,worker_key=WORKER,lease_seconds=1200,project_key=PILOT_PROJECT,job_id=row['job_id'])
        if not claim.get('claimed'):return {'ok':False,'claimed':False,'claim':claim}
        unit='control-plane-v2-worker@'+safe_systemd_instance(row['job_id'])+'.service'
        try:
            proc=subprocess.run(['systemctl','--user','start','--no-block',unit],capture_output=True,text=True,timeout=10,env=systemd_user_env())
            started=proc.returncode==0
        except (OSError,subprocess.TimeoutExpired):started=False
        if not started:
            complete_job(con,row['job_id'],worker_key=WORKER,status='Blocked',message='Worker service failed to start; needs review.')
            event(con,'ERROR','queue_manager','Failed to start worker unit',job_id=row['job_id'],data={'unit':unit})
            return {'ok':False,'claimed':False,'reason':'WORKER_START_FAILED','unit':unit}
        event(con,'INFO','queue_manager','Bounded worker unit queued',job_id=row['job_id'],data={'unit':unit})
        return {'ok':True,'claimed':True,'job_id':row['job_id'],'unit':unit,'lease_until':claim['locked_until']}
    return {'ok':True,'claimed':False,'reason':'NO_VALID_APPROVED_CODING_TICKETS'}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    opts=parser.parse_args()
    print(json.dumps(run_once(apply=opts.apply),sort_keys=True))
