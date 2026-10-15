"""One-time *interactive* VPS credential setup. Never takes secrets as command arguments."""
import getpass, json, os, stat, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from github_publisher import REPO, TOKEN_FILE
SLACK_FILE=Path('/home/aioperator/worker/credentials/ai-control-plane/slack-webhook-url')

def save_private(path,value):
    if path.is_symlink() or path.exists():raise RuntimeError('credential_already_exists_do_not_overwrite')
    if (path.parent.stat().st_mode & 0o077)!=0:raise RuntimeError('credential_directory_not_private')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as f:
            f.write(value.strip()+'\n');f.flush();os.fsync(f.fileno())
    except Exception:
        path.unlink(missing_ok=True);raise

def verify_github(value):
    req=urllib.request.Request('https://api.github.com/repos/'+REPO,
        headers={'Authorization':'Bearer '+value,'Accept':'application/vnd.github+json','User-Agent':'AI-Control-Plane-Credential-Setup'})
    try:
        with urllib.request.urlopen(req,timeout=15) as r:data=json.loads(r.read(100000))
    except urllib.error.HTTPError as e:raise ValueError('github_auth_failed_http_'+str(e.code)) from None
    if not (data.get('permissions') or {}).get('push'):
        raise ValueError('github_repo_write_permission_missing')
    return True

def verify_slack(value):
    parsed=urllib.parse.urlparse(value)
    return parsed.scheme=='https' and parsed.hostname=='hooks.slack.com' and parsed.path.startswith('/services/') and len(parsed.path.split('/'))>=5 and not parsed.username and not parsed.password

def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ('github','slack'):
        print('Usage: python3 provision_credentials.py github|slack');return 2
    kind=sys.argv[1]
    path=TOKEN_FILE if kind=='github' else SLACK_FILE
    if path.exists():print('Already configured. Refusing to replace an existing credential.');return 2
    value=getpass.getpass('Paste '+kind+' credential (hidden, never printed): ').strip()
    if not value or len(value)>2048:print('Invalid credential length');return 2
    try:
        if kind=='github':verify_github(value)
        elif not verify_slack(value):raise ValueError('invalid_slack_webhook_host_or_path')
        save_private(path,value)
    except (ValueError,RuntimeError,OSError,urllib.error.URLError) as ex:
        print('Credential was not saved:',str(ex)[:110]);return 2
    print(kind+' credential saved with restricted permissions. No secret displayed.')
    return 0
if __name__=='__main__':sys.exit(main())
