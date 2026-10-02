"""Add a pinned dataset and normalised evidence records. Never replaces a dataset."""
import hashlib
import json
import os
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb

ROOT=Path(__file__).resolve().parents[1]


def main():
    path=Path(os.getenv('MODEL_DATA_PATH',ROOT/'data/project.json'))
    payload=json.loads(path.read_text());version=hashlib.sha256(path.read_bytes()).hexdigest()
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        c.execute((ROOT/'db/001_schema.sql').read_text())
        c.execute('INSERT INTO dataset_version(data_version,payload,status) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',(version,Jsonb(payload),'Draft'))
        for s in payload['sources']:
            c.execute('INSERT INTO source_document(source_id,name,source_uri,content_sha256,model_version,classification,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                      (s['id'],s['file'],s['path'],s['sha256'],'0.2.0',s['classification'],Jsonb(s)))
        # Input records retain document aliases/locators as metadata when exact source_id mapping needs review.
        for domain in ['freight_markets','screenline_counts','station_catchment_outputs','historical_rail_freight']:
            for i,row in enumerate(payload[domain]):
                key=f'{version}:{domain}:{i}'
                c.execute('INSERT INTO input_record(input_id,source_locator,original_value,transformed_value,transformation,source_year,geography,unit,status,model_version,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                          (key,row.get('source_cell'),Jsonb(row),Jsonb(row),'Structured source-cell extraction; no silent population expansion',row.get('year'),row.get('origin') or row.get('station') or row.get('name'),row.get('unit','Source-defined; see metadata'),row.get('status','Draft'),'0.2.0',Jsonb({'dataset_version':version,'domain':domain})))
        for i,r in enumerate(payload['screenline_counts']):
            c.execute('INSERT INTO traffic_count VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s) ON CONFLICT DO NOTHING',
                      (f'{version}:{i}',r['station'],r['direction'],r['period'],r['duration_hours'],'PCU total',r['value'],r['unit'],r['status']))
        for i,r in enumerate(payload['passenger_survey']['sample_od']):
            c.execute('INSERT INTO od_cell VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s) ON CONFLICT DO NOTHING',
                      ('survey-'+version,r['origin'],r['destination'],2026,'reported sample',r['respondents'],'respondents','survey','Unresolved population expansion'))
    print(json.dumps({'data_version':version,'status':'imported; pin MODEL_DATABASE_DATA_VERSION to use'}))


if __name__=='__main__':main()
