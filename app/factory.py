import json,os,sys,time,uuid,threading
from datetime import datetime,timezone,timedelta
from psycopg.types.json import Jsonb
from .db import DATA,connect,init,setting,set_setting,audit
from .ingest import ingest
from .modeling import train
UTC=timezone.utc
def heartbeat():
 while True:
  (DATA/'factory-heartbeat').write_text(str(time.time()))
  time.sleep(20)
def main():
 init()
 threading.Thread(target=heartbeat,daemon=True).start()
 # This session-wide database lock also excludes accidental duplicate workers.
 lock=connect();lock.autocommit=True
 if not lock.execute('SELECT pg_try_advisory_lock(30301) locked').fetchone()['locked']: raise RuntimeError('Another Model Factory owns the worker lock')
 with connect() as c:
  c.execute("UPDATE jobs SET status='interrupted',ended_at=now(),detail=%s WHERE status='running'",(Jsonb({'reason':'Worker restarted; retry by submitting a new job.'}),))
 while True:
  with connect() as c: job=c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
  if job is None:
   last=setting('last_ingest',{}).get('at')
   due=not setting('reference_model') or not last or datetime.now(UTC)-datetime.fromisoformat(last)>timedelta(hours=1)
   if due:
    with connect() as c:
     trained=setting('last_training')
     training_due=not trained or datetime.now(UTC)-datetime.fromisoformat(trained)>timedelta(days=7)
     kind='ingest_train' if training_due else 'ingest'
     c.execute("INSERT INTO jobs(id,kind,status) VALUES (%s,%s,'queued')",(str(uuid.uuid4()),kind))
   time.sleep(10);continue
  with connect() as c:
   c.execute("UPDATE jobs SET status='running',started_at=now() WHERE id=%s",(job['id'],))
  print(json.dumps({'event':'job_started','id':job['id'],'kind':job['kind']}),flush=True)
  try:
   detail={}
   if job['kind'] in ('ingest','ingest_train'): detail['ingest']=ingest()
   if job['kind'] in ('train','ingest_train'):
    detail['training']=train()
    with connect() as c: set_setting(c,'last_training',datetime.now(UTC).isoformat())
   with connect() as c:
    c.execute("UPDATE jobs SET status='succeeded',ended_at=now(),detail=%s WHERE id=%s",(Jsonb(detail),job['id']))
    audit(c,'job_succeeded',{'id':job['id'],'kind':job['kind']})
   print(json.dumps({'event':'job_succeeded','id':job['id']}),flush=True)
  except Exception as e:
   # Never log exception strings from network libraries: they can contain key-bearing URLs.
   detail={'error_type':type(e).__name__,'message':'Job failed; inspect sanitized source status and retry.'}
   with connect() as c:
    c.execute("UPDATE jobs SET status='failed',ended_at=now(),detail=%s WHERE id=%s",(Jsonb(detail),job['id']))
    set_setting(c,'last_ingest_attempt',datetime.now(UTC).isoformat())
    audit(c,'job_failed',{'id':job['id'],**detail})
   print(json.dumps({'event':'job_failed','id':job['id'],**detail}),flush=True)
   time.sleep(300)
if __name__=='__main__':
 if '--health' in sys.argv:
  try: healthy=time.time()-float((DATA/'factory-heartbeat').read_text())<120
  except (ValueError,OSError): healthy=False
  sys.exit(0 if healthy else 1)
 main()
