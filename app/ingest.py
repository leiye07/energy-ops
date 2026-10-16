import hashlib,json,math,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import requests
from psycopg.types.json import Jsonb
from .db import DATA,connect,setting,set_setting,audit
UTC=timezone.utc
URL='https://api.eia.gov/v2/electricity/rto/region-data/data/'
def now(): return datetime.now(UTC)
def parse_period(s): return datetime.fromisoformat(s).replace(tzinfo=UTC)
def download(authority,start,end):
 key=Path('/run/secrets/eia_api_key').read_text().strip()
 if not key: raise RuntimeError('EIA credential is empty')
 offset=0
 while True:
  params={'api_key':key,'frequency':'hourly','data[0]':'value','facets[respondent][]':authority,'facets[type][]':'D','start':start.strftime('%Y-%m-%dT%H'),'end':end.strftime('%Y-%m-%dT%H'),'sort[0][column]':'period','sort[0][direction]':'asc','offset':offset,'length':5000}
  payload=None
  for attempt in range(4):
   try:
    r=requests.get(URL,params=params,timeout=(10,90))
    if r.status_code!=200: raise RuntimeError(f'EIA HTTP {r.status_code}')
    obj=r.json()
    if 'response' not in obj or 'data' not in obj['response']: raise RuntimeError('Invalid EIA response')
    # EIA echoes credentials in request metadata. Persist only data and safe metadata.
    payload={'response':obj['response'],'apiVersion':obj.get('apiVersion'),'query':{k:v for k,v in params.items() if k!='api_key'}}
    break
   except (requests.RequestException,ValueError,RuntimeError):
    if attempt==3: raise RuntimeError('EIA download failed after bounded retries') from None
    time.sleep(2**attempt)
  raw=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
  sha=hashlib.sha256(raw).hexdigest()
  folder=DATA/'raw';folder.mkdir(exist_ok=True)
  path=folder/(sha+'.json')
  if not path.exists(): path.write_bytes(raw)
  fetched=now()
  rows=payload['response']['data']
  with connect() as c:
   c.execute('INSERT INTO snapshots VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',(sha,URL,fetched,Jsonb({'authority':authority,'query':payload['query'],'apiVersion':payload['apiVersion'],'path':str(path),'rows':len(rows),'units':'MWh per one-hour period'})))
  yield sha,rows
  offset+=len(rows)
  if not rows or offset>=int(payload['response'].get('total',offset)): break
  time.sleep(.3)
def validate(row,authority):
 try:
  t=parse_period(row['period']);v=float(row['value'])
  if row['respondent']!=authority or row['type']!='D' or not math.isfinite(v) or v<=0 or t>now()+timedelta(hours=1): return None
  if row.get('value-units') not in ('megawatthours','megawatthours per hour','megawatts'): return None
  return t,v
 except (ValueError,KeyError,TypeError): return None
def collect(authority,start,end):
 count=0
 for sha,rows in download(authority,start,end):
  quarantine=[]
  with connect() as c:
   for row in rows:
    valid=validate(row,authority)
    if valid is None: quarantine.append(row);continue
    t,v=valid
    old=c.execute('SELECT value,snapshot_sha FROM observations WHERE authority=%s AND period=%s',(authority,t)).fetchone()
    if old is None or old['value']!=v:
     c.execute('INSERT INTO revisions(authority,period,old_value,new_value,snapshot_sha) VALUES (%s,%s,%s,%s,%s)',(authority,t,old['value'] if old else None,v,sha))
     c.execute('INSERT INTO observations(authority,period,value,snapshot_sha) VALUES (%s,%s,%s,%s) ON CONFLICT(authority,period) DO UPDATE SET value=excluded.value,snapshot_sha=excluded.snapshot_sha,updated_at=now()',(authority,t,v,sha))
    count+=1
  if quarantine:
   folder=DATA/'quarantine';folder.mkdir(exist_ok=True)
   (folder/(sha+'.json')).write_text(json.dumps(quarantine))
 return count
def coverage(authority,start,end):
 with connect() as c:
  row=c.execute('SELECT count(*) n,min(period) first,max(period) last FROM observations WHERE authority=%s AND period BETWEEN %s AND %s',(authority,start,end)).fetchone()
 expected=int((end-start).total_seconds()/3600)+1
 return {'authority':authority,'rows':row['n'],'expected_hours':expected,'coverage':row['n']/expected,'first':row['first'].isoformat() if row['first'] else None,'last':row['last'].isoformat() if row['last'] else None}
def ingest():
 end=now().replace(minute=0,second=0,microsecond=0)-timedelta(hours=24)
 authority=setting('authority')
 if not authority:
  start=end-timedelta(days=90)
  reports=[]
  for candidate in ['CISO','ERCO','PJM']:
   collect(candidate,start,end);reports.append(coverage(candidate,start,end))
  best=max(reports,key=lambda x:x['coverage'])
  if best['coverage']<.98: raise RuntimeError('No authority passed 98% pilot coverage requirement')
  authority=best['authority']
  with connect() as c:
   set_setting(c,'authority',authority);set_setting(c,'coverage_selection',reports)
   audit(c,'authority_selected',{'authority':authority,'reports':reports})
 with connect() as c:
  first=c.execute('SELECT min(period) first FROM observations WHERE authority=%s',(authority,)).fetchone()['first']
  last=c.execute('SELECT max(period) last FROM observations WHERE authority=%s',(authority,)).fetchone()['last']
 start=end-timedelta(days=400) if first is None or first>end-timedelta(days=390) else last-timedelta(days=7)
 count=collect(authority,start,end)
 report=coverage(authority,end-timedelta(days=90),end)
 with connect() as c:
  set_setting(c,'last_ingest',{'at':now().isoformat(),'authority':authority,'rows_processed':count,'coverage':report})
 return {'authority':authority,'rows_processed':count,'coverage':report}
