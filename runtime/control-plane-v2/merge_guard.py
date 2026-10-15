"""Explicit Slack-approved merge guard. No coding, no deploy, bounded retries."""
import json,re,sys,tempfile,time,urllib.parse
from pathlib import Path
from engine import init_db,event,enqueue_outbox,now_iso
from coding_executor import prepare_ticket,run,ExecutionFailed
from github_publisher import GitHubPublisher,CredentialUnavailable,PublishError,REPO
from watchdog import post_slack_webhook

PR_PREFIX='https://github.com/'+REPO+'/pull/'

def approval_candidate(con,job_id=''):
    if job_id:
        if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}',job_id):raise ValueError('invalid_job_id')
        rows=con.execute("SELECT * FROM jobs WHERE job_id=? AND status='Needs Review' AND pr_url LIKE ?",(job_id,PR_PREFIX+'%')).fetchall()
    else:
        rows=con.execute("SELECT * FROM jobs WHERE status='Needs Review' AND pr_url LIKE ? LIMIT 2",(PR_PREFIX+'%',)).fetchall()
    if len(rows)!=1:raise ValueError('Specify exact JOB ID to approve; found '+str(len(rows))+' eligible jobs')
    row=rows[0];data=json.loads(row['raw_json'] or '{}')
    ticket=prepare_ticket(data['coding_ticket']);pub=data['publisher_result']
    if ticket['job_id']!=row['job_id'] or pub['pr_url']!=row['pr_url'] or not re.fullmatch(r'[0-9a-f]{40}',pub['head_sha']):
        raise ValueError('approval_metadata_invalid')
    if row['pr_url']!=PR_PREFIX+str(pub['pr_number']):raise ValueError('pull_request_mismatch')
    return row,ticket,pub

def approve(con,job_id,user):
    if user!='U0C2MN0DU49':raise ValueError('unauthorized_approver')
    row,ticket,pub=approval_candidate(con,job_id)
    existing=con.execute("SELECT 1 FROM approvals WHERE job_id=? AND status IN ('Approved','Processing','Completed') LIMIT 1",(row['job_id'],)).fetchone()
    if existing:raise ValueError('approval_already_exists')
    import uuid
    key='approval-'+uuid.uuid4().hex[:16]
    con.execute("INSERT INTO approvals(approval_id,job_id,approval_type,status,approved_by,approval_text,pr_url,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (key,row['job_id'],'Merge','Approved',user,json.dumps({'expected_head_sha':pub['head_sha'],'pr_number':pub['pr_number']}),row['pr_url'],now_iso()))
    event(con,'INFO','merge_guard','Explicit reviewed PR approval accepted',job_id=row['job_id'],data={'approval_id':key,'pr_url':row['pr_url'],'expected_sha':pub['head_sha']})
    return {'job_id':row['job_id'],'approval_id':key,'pr_url':row['pr_url']}

def assert_pr(pr,ticket,pub,expected):
    if pr.get('state')!='open' or pr.get('merged') or pr.get('draft'):
        raise ValueError('pr_not_open_reviewable')
    if (pr.get('head') or {}).get('repo',{}).get('full_name')!=REPO:raise ValueError('pr_wrong_head_repo')
    if (pr.get('head') or {}).get('ref')!=ticket['branch']:raise ValueError('pr_head_branch_changed')
    if (pr.get('base') or {}).get('ref')!='main':raise ValueError('pr_target_changed')
    if expected and (pr.get('head') or {}).get('sha')!=expected:raise ValueError('pr_commit_changed_after_approval')

def check_statuses(api,head):
    data=api.api('GET','/repos/'+REPO+'/commits/'+head+'/check-runs')
    for check in data.get('check_runs',[]):
        if check.get('status')!='completed' or check.get('conclusion') not in ('success','neutral','skipped'):
            raise ValueError('github_checks_not_clean')

def approve_and_merge(job_id):
    con=init_db()
    row,ticket,pub=approval_candidate(con,job_id)
    approval=con.execute("SELECT * FROM approvals WHERE job_id=? AND status='Approved' ORDER BY created_at DESC LIMIT 1",(job_id,)).fetchone()
    if approval is None:raise ValueError('no_explicit_slack_approval')
    payload=json.loads(approval['approval_text'] or '{}')
    if payload.get('expected_head_sha')!=pub['head_sha'] or payload.get('pr_number')!=pub['pr_number']:
        raise ValueError('approval_does_not_match_pr')
    changed=con.execute("UPDATE approvals SET status='Processing' WHERE approval_id=? AND status='Approved'",(approval['approval_id'],)).rowcount
    if changed!=1:raise ValueError('approval_already_processing')
    try:
        github=GitHubPublisher();github.verify_write_scope()
        api='/repos/'+REPO
        pr=github.api('GET',api+'/pulls/'+str(pub['pr_number']))
        assert_pr(pr,ticket,pub,pub['head_sha'])
        main=github.api('GET',api+'/git/ref/heads/main')['object']['sha']
        head=pub['head_sha']
        if pr['base']['sha']!=main:
            github.api('PUT',api+'/pulls/'+str(pub['pr_number'])+'/update-branch',{'expected_head_sha':head})
            for _ in range(7):
                time.sleep(2)
                pr=github.api('GET',api+'/pulls/'+str(pub['pr_number']))
                if pr['base']['sha']==main and pr['head']['sha']!=head:break
            else:raise ValueError('update_branch_timeout_review_required')
            assert_pr(pr,ticket,pub,None)
            head=pr['head']['sha']
        if pr.get('mergeable') is False:raise ValueError('pr_has_merge_conflict')
        check_statuses(github,head)
        with tempfile.TemporaryDirectory(prefix='merge-review-',dir='/home/aioperator/control-plane/state/control-plane-v2/worktrees') as tmp:
            repo=Path(tmp)/'repo'
            run(['git','clone','--depth','30','--branch',ticket['branch'],'https://github.com/'+REPO+'.git',str(repo)],tmp,90)
            run(['git','fetch','--depth','30','origin','main'],repo,60)
            if run(['git','rev-parse','HEAD'],repo)!=head:raise ValueError('fetched_commit_mismatch')
            run(['git','merge-base','--is-ancestor','origin/main','HEAD'],repo,20)
            diff=run(['git','diff','--name-only','origin/main','HEAD'],repo,20).splitlines()
            if not diff or len(diff)>8 or any(x not in ticket['files_allowed'] for x in diff):raise ValueError('merge_diff_outside_ticket')
            run(ticket['command'].split(),repo,min(300,ticket['max_runtime']))
            if run(['git','status','--porcelain'],repo):raise ValueError('tests_modified_repo')
        again=github.api('GET',api+'/pulls/'+str(pub['pr_number']))
        assert_pr(again,ticket,pub,head)
        latest_main=github.api('GET',api+'/git/ref/heads/main')['object']['sha']
        if latest_main!=main:raise ValueError('main_moved_during_tests')
        check_statuses(github,head)
        merged=github.api('PUT',api+'/pulls/'+str(pub['pr_number'])+'/merge',{'sha':head,'merge_method':'merge','commit_title':'Merge approved AI ticket '+job_id})
        if not merged.get('merged'):raise ValueError('github_merge_did_not_confirm')
        con.execute("UPDATE jobs SET status='Done',current_step=?,locked_by=NULL,locked_until=NULL,updated_at=? WHERE job_id=?",
            ('Approved PR merged after latest-main refresh and tests.',now_iso(),job_id))
        con.execute("UPDATE approvals SET status='Completed' WHERE approval_id=?",(approval['approval_id'],))
        enqueue_outbox(con,'job',job_id,{'job_id':job_id,'status':'Done','pr_url':row['pr_url'],'updated_at':now_iso()})
        event(con,'INFO','merge_guard','PR merged after signed Slack approval and tests',job_id=job_id,data={'pr_url':row['pr_url'],'head_sha':head})
        from slack_replies import notify
        notify(job_id,'Merged approved '+job_id+': '+row['pr_url']+' (tests passed)')
        return {'ok':True,'job_id':job_id,'pr_url':row['pr_url']}
    except Exception as exc:
        con.execute("UPDATE approvals SET status='Failed' WHERE approval_id=?",(approval['approval_id'],))
        event(con,'ERROR','merge_guard','Merge stopped without confirmed success',job_id=job_id,data={'reason':type(exc).__name__,'message':str(exc)[:120]})
        from slack_replies import notify
        notify(job_id,'Merge stopped for '+job_id+': '+type(exc).__name__+'. PR remains for review: '+row['pr_url'])
        raise

if __name__=='__main__':
    try:print(json.dumps(approve_and_merge(sys.argv[1] if len(sys.argv)>1 else '')))
    except Exception as e:print(json.dumps({'ok':False,'error':type(e).__name__}));sys.exit(3)
