"""Short-lived Slack replies without a standing webhook. Never expose response_url."""
import hashlib,hmac,json,os,sqlite3,time,urllib.parse,urllib.request,urllib.error
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from engine import init_db
from watchdog import post_slack_webhook

SLACK_SIGNING_SECRET_FILE='/home/aioperator/worker/credentials/ai-control-plane/slack-signing-secret'
DOMAIN='hooks.slack.com'
MAX_AGE=28*60
MAX_SENDS=4

def secret_key():
    with open(SLACK_SIGNING_SECRET_FILE,'rb') as f: secret=f.read().strip()
    if len(secret)<20: raise ValueError('slack_signing_secret_not_configured')
    return hashlib.sha256(b'AI_CONTROL_PLANE_V2_RESPONSE_URL_V1:'+secret).digest()

def allowed_url(value):
    if not isinstance(value,str) or len(value)>2000: return False
    try:
        parsed=urllib.parse.urlsplit(value)
        return (parsed.scheme=='https' and parsed.hostname==DOMAIN and parsed.port in (None,443)
          and not parsed.username and not parsed.password and parsed.path.startswith('/commands/')
          and not parsed.fragment and not parsed.query)
    except ValueError:return False

def setup(con):
    con.execute('''CREATE TABLE IF NOT EXISTS slack_response_urls(
       job_id TEXT PRIMARY KEY, encrypted_url BLOB NOT NULL,
       created_at INTEGER NOT NULL, sent_count INTEGER NOT NULL DEFAULT 0
    )''')

def remember(job_id,response_url):
    if not allowed_url(response_url):return {'saved':False,'reason':'missing_or_invalid_response_url'}
    if not isinstance(job_id,str) or not (3<=len(job_id)<=64):return {'saved':False,'reason':'invalid_job_id'}
    con=init_db()
    setup(con)
    con.execute('DELETE FROM slack_response_urls WHERE created_at<?',(int(time.time())-MAX_AGE-60,))
    nonce=os.urandom(12)
    sealed=nonce+AESGCM(secret_key()).encrypt(nonce,response_url.encode('utf-8'),job_id.encode('utf-8'))
    con.execute('INSERT INTO slack_response_urls(job_id,encrypted_url,created_at,sent_count) VALUES(?,?,?,0) '
                'ON CONFLICT(job_id) DO UPDATE SET encrypted_url=excluded.encrypted_url,created_at=excluded.created_at,sent_count=0',
                (job_id,sealed,int(time.time())))
    return {'saved':True,'job_id':job_id,'valid_for_seconds':MAX_AGE}

def callback_ready(job_id):
    con=init_db();setup(con)
    row=con.execute('SELECT created_at,sent_count FROM slack_response_urls WHERE job_id=?',(job_id,)).fetchone()
    return bool(row and time.time()-row['created_at']<MAX_AGE and row['sent_count']<MAX_SENDS)

def notify(job_id,text):
    con=init_db();setup(con)
    row=con.execute('SELECT encrypted_url,created_at,sent_count FROM slack_response_urls WHERE job_id=?',(job_id,)).fetchone()
    if row and time.time()-row['created_at']<MAX_AGE and row['sent_count']<MAX_SENDS:
        try:
            sealed=row['encrypted_url']
            url=AESGCM(secret_key()).decrypt(sealed[:12],sealed[12:],job_id.encode('utf-8')).decode('utf-8')
            if not allowed_url(url):raise ValueError('invalid_saved_response_url')
            payload=json.dumps({'response_type':'in_channel','text':str(text)[:1400]}).encode('utf-8')
            req=urllib.request.Request(url,data=payload,headers={'Content-Type':'application/json'},method='POST')
            with urllib.request.urlopen(req,timeout=7) as r:
                if 200<=r.status<300:
                    con.execute('UPDATE slack_response_urls SET sent_count=sent_count+1 WHERE job_id=?',(job_id,))
                    return {'sent':True,'method':'signed_slack_response_url'}
        except (ValueError,InvalidTag,urllib.error.URLError,TimeoutError,OSError):
            pass
    fallback=post_slack_webhook(str(text)[:1400])
    return {'sent':bool(fallback.get('sent')),'method':'incoming_webhook' if fallback.get('sent') else 'none','reason':'No usable Slack callback or standing webhook' if not fallback.get('sent') else ''}
