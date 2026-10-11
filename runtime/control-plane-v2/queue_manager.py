import argparse, json, os, subprocess, time
from engine import init_db, claim_next_job, complete_job, event, systemd_user_env, safe_systemd_instance

WORKER = os.environ.get('CONTROL_PLANE_V2_QUEUE_WORKER', 'v2-queue-manager')


def start_worker_unit(job_id):
    unit = 'control-plane-v2-worker@' + safe_systemd_instance(job_id) + '.service'
    cp = subprocess.run(['systemctl', '--user', 'start', unit], text=True, capture_output=True, timeout=10, env=systemd_user_env())
    return {'unit': unit, 'ok': cp.returncode == 0, 'returncode': cp.returncode, 'stdout': cp.stdout.strip()[:400], 'stderr': cp.stderr.strip()[:400]}


def run_once(apply=False, complete_placeholder=False):
    con = init_db()
    claim = claim_next_job(con, worker_key=WORKER)
    if not claim.get('claimed'):
        return {'ok': True, 'claimed': False, 'claim': claim}
    result = {'ok': True, 'claimed': True, 'claim': claim, 'worker_start': None, 'complete': None}
    if apply:
        result['worker_start'] = start_worker_unit(claim['job_id'])
        event(con, 'INFO', 'queue_manager', 'Worker unit start attempted', job_id=claim['job_id'], data=result['worker_start'])
    if complete_placeholder:
        # The placeholder worker does no real work. Complete to Needs Review so phone/user sees it.
        time.sleep(0.2)
        result['complete'] = complete_job(con, claim['job_id'], worker_key=WORKER)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--complete-placeholder', action='store_true')
    args = ap.parse_args()
    print(json.dumps(run_once(apply=args.apply, complete_placeholder=args.complete_placeholder), sort_keys=True))

if __name__ == '__main__':
    main()
