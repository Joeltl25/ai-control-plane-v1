"""Strict preflight for bounded Ubuntu coding tickets. No execution or secrets."""
import json,re
from pathlib import PurePosixPath
REQUIRED=('job_id','repo','branch','exact_goal','files_allowed','files_not_allowed','evidence','command','max_attempts','max_runtime','worker_choice','stop_rules','rollback_point','slack_thread')
SAFE_TEST=re.compile(r'^(pytest(?: -q)?|python3? -m pytest(?: -q)?|npm test|npm run test|go test ./\.\.\.|cargo test|bash test.sh)$')
def validate(ticket):
    if not isinstance(ticket,dict): raise ValueError('ticket must be object')
    missing=[k for k in REQUIRED if not ticket.get(k)]
    if missing: raise ValueError('missing fields: '+','.join(missing))
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}',ticket['job_id']): raise ValueError('invalid job id')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',ticket['repo']): raise ValueError('repo must be owner/name')
    if not re.fullmatch(r'ai/[a-z0-9][a-z0-9/_-]{2,100}',ticket['branch']) or '..' in ticket['branch']: raise ValueError('invalid branch')
    if ticket['worker_choice'] not in ('codex','gemini'): raise ValueError('worker not approved')
    if type(ticket['max_attempts']) is not int or not 1<=ticket['max_attempts']<=2: raise ValueError('attempt cap')
    if type(ticket['max_runtime']) is not int or not 60<=ticket['max_runtime']<=1200: raise ValueError('runtime cap')
    if not SAFE_TEST.fullmatch(ticket['command']): raise ValueError('test command not allowlisted')
    for field in ('files_allowed','files_not_allowed'):
        files=ticket[field]
        if not isinstance(files,list) or not files or len(files)>8: raise ValueError('invalid file list')
        for f in files:
            if not isinstance(f,str) or not f or f.startswith('/') or '\\' in f or any(x in ('..','.') for x in PurePosixPath(f).parts) or any(x in f.lower() for x in ('.env','credential','secret','.ssh')):raise ValueError('unsafe path')
    if set(ticket['files_allowed'])&set(ticket['files_not_allowed']):raise ValueError('conflicting file boundaries')
    return {'ok':True,'job_id':ticket['job_id'],'worker':ticket['worker_choice'],'files':len(ticket['files_allowed'])}
if __name__=='__main__':
    import sys
    try: print(json.dumps(validate(json.load(sys.stdin))))
    except (ValueError,KeyError,TypeError) as e: print(json.dumps({'ok':False,'error':str(e)}));sys.exit(2)
