import json
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from engine import init_db, bootstrap, emergency_stop, counts

HOST = os.environ.get('CONTROL_PLANE_V2_HOST', '127.0.0.1')
PORT = int(os.environ.get('CONTROL_PLANE_V2_PORT', '8801'))
DB = os.environ.get('CONTROL_PLANE_V2_DB', '/home/aioperator/control-plane/state/control-plane-v2/control-plane.sqlite')


class Handler(BaseHTTPRequestHandler):
    server_version = 'control-plane-v2/0.1'

    def log_message(self, fmt, *args):
        print(json.dumps({'event': 'request', 'client': self.client_address[0], 'method': self.command, 'path': self.path, 'message': fmt % args}))

    def write(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('cache-control', 'no-store')
        self.send_header('content-length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        n = int(self.headers.get('content-length') or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode('utf-8'))

    def do_GET(self):
        if self.path != '/health':
            return self.write(404, {'ok': False, 'error': 'not_found'})
        con = init_db(DB)
        return self.write(200, {'ok': True, 'service': 'control-plane-v2', 'db': DB, 'counts': counts(con)})

    def do_POST(self):
        try:
            payload = self.read_json()
            con = init_db(DB)
            if self.path == '/bootstrap_or_continue':
                return self.write(200, bootstrap(con, payload.get('intent', 'CONTINUE'), payload.get('project', '') or payload.get('target', '')))
            if self.path == '/stop':
                return self.write(200, emergency_stop(con, payload.get('project', '') or payload.get('target', '')))
            return self.write(404, {'ok': False, 'error': 'not_found'})
        except Exception as e:
            return self.write(500, {'ok': False, 'error': str(e)[:500]})


def main():
    init_db(DB)
    print(json.dumps({'event': 'control_plane_v2_started', 'host': HOST, 'port': PORT, 'db': DB}))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
