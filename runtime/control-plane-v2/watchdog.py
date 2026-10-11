import argparse, json, os, subprocess
from engine import init_db, counts, expire_old_leases, event, now_iso

DB = os.environ.get('CONTROL_PLANE_V2_DB','/home/aioperator/control-plane/state/control-plane-v2/control-plane.sqlite')
WATCHED_UNITS = [
    'control-plane-v1-gateway.service',
    'control-plane-v2-gateway.service',
    'control-plane-v2-airtable-mirror.timer',
]
OPTIONAL_UNITS = [
    'control-plane-v2-queue-manager.timer',
]

def systemd(args):
    uid = str(os.getuid())
    env = os.environ.copy()
    env.setdefault('XDG_RUNTIME_DIR', '/run/user/' + uid)
    env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/' + uid + '/bus')
    cp = subprocess.run(['systemctl','--user'] + list(args), text=True, capture_output=True, timeout=8, env=env)
    return cp.returncode, cp.stdout.strip(), cp.stderr.strip()

def unit_state(unit):
    rc, out, err = systemd(['is-active', unit])
    return {'unit': unit, 'active': out or 'unknown', 'ok': rc == 0, 'error': err[:250]}

def outbox_state(con):
    rows = con.execute("SELECT status, COUNT(*) c FROM sync_outbox GROUP BY status").fetchall()
    return {r['status']: r['c'] for r in rows}

def lease_state(con):
    rows = con.execute("SELECT status, COUNT(*) c FROM leases GROUP BY status").fetchall()
    return {r['status']: r['c'] for r in rows}

def run_once(apply=False):
    con = init_db(DB)
    expired = expire_old_leases(con) if apply else 0
    required = [unit_state(u) for u in WATCHED_UNITS]
    optional = [unit_state(u) for u in OPTIONAL_UNITS]
    outbox = outbox_state(con)
    leases = lease_state(con)
    ok = all(u['ok'] for u in required) and outbox.get('Retry', 0) == 0
    result = {
        'ok': ok,
        'ts': now_iso(),
        'apply': apply,
        'expired_leases': expired,
        'required_units': required,
        'optional_units': optional,
        'counts': counts(con),
        'outbox': outbox,
        'leases': leases,
        'note': 'No Slack bot token present on VPS; alerts are written to SQLite/Airtable mirror for now.'
    }
    level = 'INFO' if ok else 'WARN'
    event(con, level, 'watchdog', 'V2 watchdog health check', data=result)
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    print(json.dumps(run_once(apply=args.apply), sort_keys=True))

if __name__ == '__main__':
    main()
