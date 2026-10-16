import os
from pathlib import Path
from urllib.parse import quote
password=quote(Path('/run/secrets/db_password').read_text().strip(),safe='')
os.environ['MLFLOW_BACKEND_STORE_URI']=f'postgresql+psycopg2://energyops:{password}@postgres:5432/mlflow'
os.execvp('mlflow',['mlflow','server','--host','0.0.0.0','--port','5000','--serve-artifacts','--artifacts-destination','/artifacts','--allowed-hosts','mlflow:5000,localhost:5000,127.0.0.1:5000','--workers','1'])
