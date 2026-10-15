"""GitHub REST publisher for a single approved repo. No credential is given to agents."""
import base64, json, os, stat, urllib.error, urllib.parse, urllib.request
from pathlib import Path

REPO = 'Joeltl25/ai-control-plane-v1'
BASE = 'https://api.github.com'
TOKEN_FILE = Path('/home/aioperator/worker/credentials/ai-control-plane/github-control-plane-pat')

class CredentialUnavailable(Exception): pass
class PublishError(Exception): pass

def token_status():
    try:
        st=TOKEN_FILE.stat()
        if TOKEN_FILE.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
            return False,'credential_file_permissions_unsafe'
        if st.st_size < 25 or st.st_size > 1024:
            return False,'credential_file_size_invalid'
    except FileNotFoundError:
        return False,'github_credential_missing'
    return True,'present'

class GitHubPublisher:
    def __init__(self):
        ok,reason=token_status()
        if not ok: raise CredentialUnavailable(reason)
        self._token=TOKEN_FILE.read_text(encoding='utf-8').strip()
    def api(self,method,path,payload=None):
        if not path.startswith('/repos/'+REPO+'/'):raise PublishError('unapproved github endpoint')
        data=None if payload is None else json.dumps(payload).encode('utf-8')
        req=urllib.request.Request(BASE+path, data=data,method=method,headers={
          'Authorization':'Bearer '+self._token,'Accept':'application/vnd.github+json',
          'X-GitHub-Api-Version':'2022-11-28','User-Agent':'AI-Control-Plane-V2',
          'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=18) as resp:
                return json.loads(resp.read(1024*1024).decode('utf-8'))
        except urllib.error.HTTPError as e:
            raise PublishError('github_http_'+str(e.code)) from None
        except (urllib.error.URLError,TimeoutError) as e:
            raise PublishError('github_transport_error') from None
    def verify_write_scope(self):
        p=self.api('GET','/repos/'+REPO+'/')
        perm=p.get('permissions') or {}
        if not perm.get('push'):raise CredentialUnavailable('github_repo_push_permission_missing')
        return True
    def publish(self,branch,base_sha,files,job_id):
        if not branch.startswith('ai/') or not files or len(files)>8:raise PublishError('invalid request')
        path='/repos/'+REPO
        main=self.api('GET',path+'/git/ref/heads/main')['object']['sha']
        if main!=base_sha:raise PublishError('main_changed_since_tests')
        try:
            self.api('GET',path+'/git/ref/heads/'+urllib.parse.quote(branch,safe='/'))
        except PublishError as ex:
            if str(ex)!='github_http_404':raise
        else:raise PublishError('branch_already_exists_needs_review')
        base_commit=self.api('GET',path+'/git/commits/'+base_sha)
        tree=[]
        for filename,content in files.items():
            if not isinstance(content,bytes) or len(content)>65536 or b'\0' in content:raise PublishError('file_type_or_size_blocked')
            content.decode('utf-8')
            blob=self.api('POST',path+'/git/blobs',{'content':base64.b64encode(content).decode('ascii'),'encoding':'base64'})
            tree.append({'path':filename,'mode':'100644','type':'blob','sha':blob['sha']})
        newtree=self.api('POST',path+'/git/trees',{'base_tree':base_commit['tree']['sha'],'tree':tree})
        commit=self.api('POST',path+'/git/commits',{'message':'AI ticket '+job_id+' (bounded worker)', 'tree':newtree['sha'],'parents':[base_sha]})
        self.api('POST',path+'/git/refs',{'ref':'refs/heads/'+branch,'sha':commit['sha']})
        pr=self.api('POST',path+'/pulls',{'title':'AI ticket '+job_id,'head':branch,'base':'main','draft':False,
          'body':'Automated bounded ticket. Local tests passed, file allowlist checked. Review GitHub diff and tests. No automatic merge or deploy.'})
        if not str(pr.get('html_url','')).startswith('https://github.com/'+REPO+'/pull/'):
            raise PublishError('unexpected_pr_url')
        return {'pr_url':pr['html_url'],'pr_number':pr['number'],'head_sha':commit['sha'],'branch':branch}
