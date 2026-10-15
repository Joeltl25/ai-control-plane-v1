"""Scoped job runner: clean clone, Codex workspace sandbox, tests, diff guard."""
import os, re, subprocess, tempfile, json
from pathlib import Path
from ticket_validator import validate
from github_publisher import REPO, GitHubPublisher, CredentialUnavailable

ROOT=Path('/home/aioperator/control-plane/state/control-plane-v2/worktrees')
CODEX='/home/aioperator/.local/bin/codex'

class ExecutionFailed(Exception): pass

def run(argv,cwd,timeout=30,stdin=None):
    try:
        result=subprocess.run(argv,cwd=str(cwd),input=stdin,text=True,capture_output=True,timeout=timeout,
            env={'HOME':'/home/aioperator','USER':'aioperator','PATH':'/home/aioperator/.local/bin:/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','GIT_TERMINAL_PROMPT':'0','XDG_RUNTIME_DIR':'/run/user/1001'})
    except subprocess.TimeoutExpired:
        raise ExecutionFailed('step_timed_out') from None
    if result.returncode:
        raise ExecutionFailed('command_failed_'+Path(argv[0]).name+'_exit_'+str(result.returncode))
    return result.stdout.strip()

def changed_files(repo):
    raw=subprocess.run(['git','status','--porcelain=v1','-z','--untracked-files=all'],cwd=repo,capture_output=True,timeout=15,check=True).stdout
    parts=raw.split(b'\0')
    files=[]
    for item in parts:
        if not item:continue
        if len(item)<4 or item[:2] not in (b' M',b'M ',b'MM',b'??',b' A',b'A '):raise ExecutionFailed('unsupported_change_type')
        try:name=item[3:].decode('utf-8')
        except UnicodeDecodeError:raise ExecutionFailed('invalid_filename') from None
        files.append(name)
    return files

def prepare_ticket(ticket):
    validate(ticket)
    if ticket['repo']!=REPO:raise ValueError('repo_not_approved_for_pilot')
    if ticket['worker_choice']!='codex':raise ValueError('worker_not_approved_for_pilot')
    if ticket['rollback_point']!='origin/main':raise ValueError('invalid_rollback_point')
    if any(not re.fullmatch(r'[A-Za-z0-9_./-]{1,150}',p) or p.startswith('.git') for p in ticket['files_allowed']):
        raise ValueError('invalid_path_for_pilot')
    return ticket

def execute(ticket,agent=False,publish=False):
    prepare_ticket(ticket)
    if publish and not agent:raise ValueError('publishing_requires_tests')
    publisher=None
    if publish:
        publisher=GitHubPublisher()  # Refuse before spending agent quota if credential missing.
        publisher.verify_write_scope()
    if not agent:return {'ok':True,'phase':'preflight','job_id':ticket['job_id'],'agent_executed':False}
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    if any(ROOT.glob('job-'+ticket['job_id']+'-*')):raise ExecutionFailed('job_workspace_exists_stop_to_avoid_parallel_workers')
    workspace=Path(tempfile.mkdtemp(prefix='job-'+ticket['job_id']+'-',dir=ROOT))
    workspace.chmod(0o700)
    repo=workspace/'repo'
    run(['git','clone','--depth','1','--branch','main','https://github.com/'+REPO+'.git',str(repo)],workspace,120)
    base_sha=run(['git','rev-parse','HEAD'],repo)
    run(['git','checkout','-b',ticket['branch']],repo)
    prompt=('Ticket '+ticket['job_id']+'\nGoal: '+ticket['exact_goal']+'\nAllowed edited files: '+', '.join(ticket['files_allowed'])+
        '\nForbidden files: '+', '.join(ticket['files_not_allowed'])+'\nEvidence: '+ticket['evidence']+
        '\nStop rules: '+ticket['stop_rules']+'\nDo not push, merge, deploy, access credentials, or edit other paths.')
    run([CODEX,'exec','--ephemeral','--sandbox','workspace-write','-C',str(repo),'-'],repo,ticket['max_runtime'],prompt)
    names=changed_files(repo)
    if not names or len(names)>8 or any(p not in ticket['files_allowed'] for p in names):
        raise ExecutionFailed('diff_outside_exact_ticket_allowlist')
    files={}
    for name in names:
        p=repo/name
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(repo.resolve()):
            raise ExecutionFailed('nonregular_or_linked_file')
        data=p.read_bytes()
        if b'\0' in data or len(data)>65536:raise ExecutionFailed('binary_or_oversized_file')
        data.decode('utf-8')
        files[name]=data
    run(['git','diff','--check'],repo,20)
    run(ticket['command'].split(),repo,min(300,ticket['max_runtime']))
    run(['git','add','--']+names,repo)
    run(['git','-c','user.name=AI Control Plane','-c','user.email=ai-control-plane@users.noreply.github.com',
         'commit','-m','AI ticket '+ticket['job_id']],repo)
    result={'ok':True,'job_id':ticket['job_id'],'phase':'tested_local_commit','base_sha':base_sha,
        'head_sha':run(['git','rev-parse','HEAD'],repo),'files':names,'workspace':str(repo),'published':False}
    if publish:
        published=publisher.publish(ticket['branch'],base_sha,files,ticket['job_id'])
        result.update(published,phase='draft_pr_open',published=True)
    return result
