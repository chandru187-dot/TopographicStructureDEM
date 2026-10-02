"""Disposable GitHub Actions PostGIS integration test; synthetic data only."""
import hashlib,json,os,tempfile
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
from railmodel.fixture import synthetic_data
from railmodel.engine import Engine
from railmodel.store import Store
url=os.environ['DATABASE_URL'];schema=Path('db/001_schema.sql').read_text()
with psycopg.connect(url,autocommit=True)as c:
 c.execute(schema);c.execute(schema)
 version=c.execute('SELECT PostGIS_Version()').fetchone()[0]
 area=c.execute("SELECT ST_Area(ST_GeomFromText('POLYGON((0 0,0 2,2 2,2 0,0 0))',4326))").fetchone()[0]
 assert area==4
 data=synthetic_data();data_version=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
 c.execute('INSERT INTO dataset_version(data_version,payload,status) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',(data_version,Jsonb(data),'Synthetic'))
 before=c.execute('SELECT count(*) FROM dataset_version').fetchone()[0]
 c.execute('INSERT INTO dataset_version(data_version,payload,status) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',(data_version,Jsonb(data),'Synthetic'))
 assert c.execute('SELECT count(*) FROM dataset_version').fetchone()[0]==before
os.environ['MODEL_DATABASE_DATA_VERSION']=data_version
result=Engine().run({'year':2045})
with Store(url)as store:
 store.insert(result);assert store.get(result['run_id'])['input_snapshot']==data
with Store(url)as store:assert any(x['run_id']==result['run_id']for x in store.list())
print(json.dumps({'status':'PASS','migration_idempotent':True,'spatial_query':True,'dataset_version_immutable_insert':True,'postgres_run_snapshot_roundtrip':True,'postgis_version':version,'inputs':'SYNTHETIC ONLY'}))
