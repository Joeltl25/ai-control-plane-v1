"""Credential-holding stage: publish a tested local commit, no coding agent here."""
import json,sys
from pathlib import Path
from engine import init_db,now_iso,complete_job,event,enqueue_outbox
from github_publisher import GitHubPublisher,CredentialUnavailable,PublishError
from coding_executor import run,prepare_ticket,ExecutionFailed
from watchdog import post_slack_webhook
WORKER='v2-queue-manager'

def publish_job(job_id):
    con=init_db()
    row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
    active=con.execute("SELECT 1 FROM leases WHERE job_id=? AND locked_by=? AND status='Active'",(job_id,WORKER)).fetchone()
    if not row or row['status']!='In Progress' or row['locked_by']!=WORKER or not active or (row['locked_until'] or '')<=now_iso():
        print(json.dumps({'ok':False,'reason':'NO_ACTIVE_PUBLISH_LEASE'}));return 2
    try:
        data=json.loads(row['raw_json'] or '{}')
        ticket=prepare_ticket(data['coding_ticket'])
        result=data['worker_result']
        if ticket['job_id']!=job_id or not result.get('ok') or result.get('phase')!='tested_local_commit':
            raise ValueError('invalid_worker_evidence')
        repo=Path(result['workspace']).resolve(strict=True)
        allowed_root=Path('/home/aioperator/control-plane/state/control-plane-v2/worktrees').resolve()
        if not repo.is_relative_to(allowed_root) or repo.name!='repo':raise ValueError('unapproved_workspace')
        if run(['git','status','--porcelain'],repo):raise ValueError('workspace_has_uncommitted_changes')
        head=run(['git','rev-parse','HEAD'],repo)
        base=run(['git','rev-parse','HEAD^'],repo)
        if head!=result['head_sha'] or base!=result['base_sha']:raise ValueError('commit_changed_after_tests')
        paths=run(['git','diff','--name-only',base,head],repo).splitlines()
        if not paths or set(paths)!=set(result['files']) or any(p not in ticket['files_allowed'] for p in paths):
            raise ValueError('published_files_outside_allowlist')
        files={}
        for name in paths:
            p=repo/name
            if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(repo):raise ValueError('invalid_publish_file')
            content=p.read_bytes()
            if len(content)>65536 or b'\0' in content:raise ValueError('invalid_publish_content')
            files[name]=content
        run(ticket['command'].split(),repo,300)
        github=GitHubPublisher()
        github.verify_write_scope()
        pr=github.publish(ticket['branch'],base,files,job_id)
        data['publisher_result']=pr
        con.execute('UPDATE jobs SET raw_json=? WHERE job_id=?',(json.dumps(data),job_id))
        con.execute('UPDATE jobs SET pr_url=?,branch=?,worker=?,updated_at=? WHERE job_id=?',
            (pr['pr_url'],ticket['branch'],'codex',now_iso(),job_id))
        enqueue_outbox(con,'job',job_id,{'job_id':job_id,'pr_url':pr['pr_url'],'branch':ticket['branch'],'worker':'codex','updated_at':now_iso()})
        complete_job(con,job_id,WORKER,status='Needs Review',message='Draft PR ready: '+pr['pr_url']+'; phone review required.')
        event(con,'INFO','publisher_runner','PR published after tests',job_id=job_id,data=pr)
        from slack_replies import notify
        notification=notify(job_id,'PR ready for GitHub Mobile review: '+job_id+' '+pr['pr_url']+' . No automatic merge.')
        print(json.dumps({'ok':True,'pr_url':pr['pr_url'],'slack_sent':notification.get('sent',False)}));return 0
    except CredentialUnavailable:reason='GitHub repository credential unavailable';status='Needs Credential'
    except (ValueError,KeyError,TypeError,UnicodeError,ExecutionFailed,PublishError):
        reason='Publisher verification or GitHub operation failed; review before retry';status='Blocked'
    except Exception:
        reason='Unexpected publisher failure; review logs';status='Blocked'
    complete_job(con,job_id,WORKER,status=status,message=reason)
    event(con,'ERROR','publisher_runner','Publishing stopped',job_id=job_id,data={'status':status,'reason':reason})
    from slack_replies import notify
    notify(job_id,'VPS job '+job_id+': '+status+'. '+reason)
    print(json.dumps({'ok':False,'status':status,'reason':reason}));return 3

if __name__=='__main__':sys.exit(publish_job(sys.argv[1] if len(sys.argv)>1 else ''))
