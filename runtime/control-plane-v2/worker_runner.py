import json, sys, time
from engine import init_db, event, now_iso

def main():
    job_id = sys.argv[1] if len(sys.argv) > 1 else 'unknown'
    con = init_db()
    event(con, 'INFO', 'worker_runner', 'Worker placeholder started', job_id=job_id, data={'note':'placeholder only'})
    print(json.dumps({'ok': True, 'job_id': job_id, 'message': 'placeholder worker; no autonomous code execution yet'}))

if __name__ == '__main__':
    main()
