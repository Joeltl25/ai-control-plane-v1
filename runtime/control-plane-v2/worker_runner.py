"""Agent stage. This service never reads GitHub or Slack credentials."""
import json,sys,threading,subprocess
from engine import init_db,event,complete_job,heartbeat_job,enqueue_outbox,now_iso,systemd_user_env,safe_systemd_instance
from coding_executor import execute,prepare_ticket,ExecutionFailed
WORKER='v2-queue-manager'

def run_job(job_id):
    con=init_db()
    row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
    lease=con.execute("SELECT 1 FROM leases WHERE job_id=? AND status='Active' AND locked_by=? LIMIT 1",(job_id,WORKER)).fetchone()
    if not row or row['status']!='In Progress' or row['locked_by']!=WORKER or not lease or (row['locked_until'] or '')<=now_iso():
        print(json.dumps({'ok':False,'reason':'NO_VALID_ACTIVE_JOB_LEASE'}));return 2
    try:
        payload=json.loads(row['raw_json'] or '{}');ticket=prepare_ticket(payload['coding_ticket'])
        if ticket['job_id']!=job_id or (row['branch'] and row['branch']!=ticket['branch']):raise ValueError('job_ticket_mismatch')
    except (ValueError,KeyError,TypeError):
        complete_job(con,job_id,WORKER,'Blocked','Coding ticket invalid; requires review.')
        event(con,'WARN','worker_runner','Ticket refused',job_id=job_id)
        return 2
    done=threading.Event()
    def renew():
        while not done.wait(60):
            try:
                c=init_db()
                active=c.execute('SELECT status,locked_by FROM jobs WHERE job_id=?',(job_id,)).fetchone()
                if not active or active['status']!='In Progress' or active['locked_by']!=WORKER:return
                heartbeat_job(c,job_id,WORKER,extend_seconds=1200)
                c.close()
            except Exception:return
    thread=threading.Thread(target=renew,daemon=True);thread.start()
    try:
        result=execute(ticket,agent=True,publish=False)
        payload['worker_result']=result
        con.execute('UPDATE jobs SET raw_json=?,current_step=?,updated_at=? WHERE job_id=?',
            (json.dumps(payload), 'Local tests passed; publishing draft PR.',now_iso(),job_id))
        enqueue_outbox(con,'job',job_id,{'job_id':job_id,'current_step':'Local tests passed; publishing draft PR.','updated_at':now_iso()})
        unit='control-plane-v2-publisher@'+safe_systemd_instance(job_id)+'.service'
        proc=subprocess.run(['systemctl','--user','start','--no-block',unit],timeout=10,capture_output=True,text=True,env=systemd_user_env())
        if proc.returncode:raise ExecutionFailed('publisher_service_start_failed')
        event(con,'INFO','worker_runner','Local agent tests passed; publisher queued',job_id=job_id,data={'unit':unit,'files':result['files']})
        print(json.dumps({'ok':True,'job_id':job_id,'phase':'local_tests_passed_publish_queued'}));return 0
    except (ValueError,KeyError,TypeError,ExecutionFailed,UnicodeError) as exc:
        reason='Scoped agent stopped: '+str(exc)[:120]
    except Exception:
        reason='Scoped agent unexpected error; see worker logs.'
    finally:
        done.set();thread.join(timeout=2)
    complete_job(con,job_id,WORKER,status='Blocked',message=reason)
    event(con,'ERROR','worker_runner','Coding worker stopped',job_id=job_id,data={'reason':reason})
    print(json.dumps({'ok':False,'job_id':job_id,'reason':reason}));return 3

if __name__=='__main__':sys.exit(run_job(sys.argv[1] if len(sys.argv)>1 else ''))
