import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
DATA=Path(os.getenv('DATA_DIR','/data'))
SCHEMA='''
CREATE TABLE IF NOT EXISTS settings (key text PRIMARY KEY, value jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (sha text PRIMARY KEY, url text NOT NULL, fetched_at timestamptz NOT NULL, metadata jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS observations (authority text NOT NULL, period timestamptz NOT NULL, value double precision NOT NULL, snapshot_sha text NOT NULL REFERENCES snapshots(sha), first_seen timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(authority,period));
CREATE TABLE IF NOT EXISTS revisions (id bigserial PRIMARY KEY, authority text NOT NULL, period timestamptz NOT NULL, old_value double precision, new_value double precision NOT NULL, snapshot_sha text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS jobs (id text PRIMARY KEY, kind text NOT NULL, status text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, ended_at timestamptz, detail jsonb NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS models (id text PRIMARY KEY, kind text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), dataset_sha text NOT NULL, metadata jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS forecast_runs (id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), model_id text NOT NULL REFERENCES models(id), issue_time timestamptz NOT NULL, dataset_sha text NOT NULL, payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id bigserial PRIMARY KEY, occurred_at timestamptz NOT NULL DEFAULT now(), actor text NOT NULL, action text NOT NULL, detail jsonb NOT NULL);
'''
def connect():
 return psycopg.connect(host=os.getenv('DATABASE_HOST','postgres'),dbname='energyops',user='energyops',password=Path('/run/secrets/db_password').read_text().strip(),row_factory=dict_row,connect_timeout=10)
def init():
 with connect() as c:
  c.execute('SELECT pg_advisory_xact_lock(30300)');c.execute(SCHEMA)
 DATA.mkdir(exist_ok=True)
def setting(key,default=None):
 with connect() as c: row=c.execute('SELECT value FROM settings WHERE key=%s',(key,)).fetchone()
 return row['value'] if row else default
def set_setting(c,key,value): c.execute('INSERT INTO settings VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,Jsonb(value)))
def audit(c,action,detail,actor='model-factory'): c.execute('INSERT INTO audit(actor,action,detail) VALUES (%s,%s,%s)',(actor,action,Jsonb(detail)))
