import hmac, json, os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from engine import init_db, bootstrap, emergency_stop, counts

HOST = os.environ.get('CONTROL_PLANE_V2_HOST','127.0.0.1')
PORT = int(os.environ.get('CONTROL_PLANE_V2_PORT','8799'))
DB = os.environ.get('CONTROL_PLANE_V2_DB','/home/aioperator/control-plane/state/control-plane-v2/control-plane.sqlite')
TOKEN_FILE = os.environ.get('CONTROL_PLANE_V2_API_TOKEN_FILE','/home/aioperator/worker/credentials/ai-control-plane/control-plane-v2-api-token')

class Handler(BaseHTTPRequestHandler):
    server_version = 'control-plane-v2/0.1'
    def log_message(self, fmt, *args):
        print(json.dumps({'event':'request','client':self.client_address[0],'method':self.command,'path':self.path,'message':fmt % args}))
    def write(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('content-type','application/json; charset=utf-8')
        self.send_header('cache-control','no-store')
        self.send_header('content-length', str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def read_json(self):
        n = int(self.headers.get('content-length') or 0)
        if not n: return {}
        return json.loads(self.rfile.read(n).decode('utf-8'))
    def auth_ok(self):
        try:
            with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
                expected = f.read().strip()
        except FileNotFoundError:
            return False
        got = self.headers.get('authorization') or ''
        if not got.lower().startswith('bearer '):
            return False
        return hmac.compare_digest(got[7:].strip(), expected)

    def do_GET(self):
        if self.path != '/health': return self.write(404, {'ok':False,'error':'not_found'})
        con = init_db(DB)
        return self.write(200, {'ok':True,'service':'control-plane-v2','db':DB,'counts':counts(con)})
    def do_POST(self):
        try:
            payload = self.read_json()
            con = init_db(DB)
            if self.path == '/bootstrap_or_continue':
                return self.write(200, bootstrap(con, payload.get('intent','CONTINUE'), payload.get('project','') or payload.get('target','')))
            if self.path == '/stop':
                if not self.auth_ok():
                    return self.write(401, {'ok':False,'error':'unauthorized'})
                return self.write(200, emergency_stop(con, payload.get('project','') or payload.get('target','')))
            return self.write(404, {'ok':False,'error':'not_found'})
        except Exception as e:
            return self.write(500, {'ok':False,'error':str(e)[:500]})

def main():
    init_db(DB)
    print(json.dumps({'event':'control_plane_v2_started','host':HOST,'port':PORT,'db':DB}))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

if __name__ == '__main__':
    main()
