"""Authoritative batch entrypoint for bounded GitHub Actions jobs."""
import argparse,csv,hashlib,html,json,os,platform,resource,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from .engine import Engine
from .fixture import synthetic_data
from .store import Store
from .excel_export import export_workbook
from .bridge import materialize_request


def write_json(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False,ensure_ascii=False))
def write_csv(path,rows):
 rows=list(rows);keys=list(dict.fromkeys(k for r in rows for k in r))or['status']
 with Path(path).open('w',newline='')as f:
  w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows([{k:json.dumps(v,ensure_ascii=False)if isinstance(v,(dict,list))else v for k,v in r.items()}for r in rows])

def geometry_inventory(geo):
 rows=[]
 for i,feature in enumerate(geo.get('features',[])):
  geometry=feature.get('geometry')or{};coords=[]
  def visit(x):
   if isinstance(x,list)and len(x)>=2 and all(isinstance(v,(int,float))for v in x[:2]):coords.append(x[:2])
   elif isinstance(x,list):
    for part in x:visit(part)
  visit(geometry.get('coordinates',[]))
  if coords and any(not -180<=x<=180 or not -90<=y<=90 for x,y in coords):raise ValueError('Geography coordinates outside WGS84 envelope')
  rows.append({'feature':i,'name':feature.get('properties',{}).get('name'),'geometry':geometry.get('type'),'coordinate_count':len(coords),'xmin':min((p[0]for p in coords),default=None),'ymin':min((p[1]for p in coords),default=None),'xmax':max((p[0]for p in coords),default=None),'ymax':max((p[1]for p in coords),default=None),'status':'Inventory only; station mapping and alignment approval not inferred'})
 return rows

def execute(data_path,output,scenario=None,compare=False,policy='approved_only',synthetic=False,geometry=None):
 start=time.perf_counter();out=Path(output);out.mkdir(parents=True,exist_ok=False)
 if synthetic:
  policy='synthetic'
  data_path=out/'synthetic_input.json';write_json(data_path,synthetic_data())
 e=Engine(data_path)
 if policy=='approved_only' and e.data.get('baseline_approval',{}).get('status')!='Approved':raise ValueError('Approved numerical baseline not established; explicitly select Draft profile to inspect alternatives')
 if policy not in ['approved_only','explicit_draft','synthetic']:raise ValueError('Unknown input policy')
 if not synthetic and e.data['governance_status'].startswith('SYNTHETIC'):raise ValueError('Synthetic fixture cannot masquerade as project inputs')
 base=scenario or {'year':2045,'case':'Base'}
 selections=[base]
 if compare:
  selections=[{**base,'case':case}for case in ['Low','Base','High']]+[{**base,'overrides':{**base.get('overrides',{}),'headway_min':h}}for h in [10,20,30]]
 runs=[]
 with Store('sqlite:///'+str(out/'runs.sqlite'))as store:
  for selection in selections:
   r=e.run(selection)
   if synthetic:r['validation_status']='SYNTHETIC_TEST_ONLY';r['warnings'].insert(0,'PUBLIC SYNTHETIC EXECUTION FIXTURE — no Sabah Rail forecast data')
   store.insert(r);write_json(out/f"run-{r['run_id']}.json",r);runs.append(r)
 write_json(out/'input_snapshot.json',e.data)
 write_json(out/'runs.json',[{k:v for k,v in r.items()if k!='input_snapshot'}for r in runs])
 write_csv(out/'source_register.csv',e.data['sources']);write_csv(out/'unresolved_register.csv',e.data['unresolved'])
 write_csv(out/'run_register.csv',[{k:r[k]for k in ['run_id','timestamp','model_version','git_version','data_version','scenario','validation_status']}for r in runs])
 write_csv(out/'freight_od.csv',[{'run_id':r['run_id'],**x}for r in runs for x in r['freight']['od']])
 write_csv(out/'qa.csv',[{'run_id':r['run_id'],**x}for r in runs for x in r['checks']])
 write_csv(out/'scenario_comparison.csv',[{'run_id':r['run_id'],'case':r['scenario']['case'],'year':r['scenario']['year'],'headway_min':r['operations']['headway_min'],'passenger_trips_day':r['passenger']['daily_source_trips'],'freight_tonnes_year':r['freight']['tonnes_year'],'net_vkt_avoided':r['traffic']['net_vkt_avoided'],'validation_status':r['validation_status']}for r in runs])
 if geometry:write_csv(out/'geography_inventory.csv',geometry_inventory(json.loads(Path(geometry).read_text())))
 export_workbook(out/'Sabah_Rail_Audit.xlsx',runs,e.data)
 status='SYNTHETIC TEST ONLY'if synthetic else'DRAFT · NOT APPROVED FORECAST'
 body=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>'for v in [r['scenario']['case'],r['scenario']['year'],round(r['passenger']['daily_source_trips'],2),round(r['freight']['tonnes_year'],2),r['validation_status']])+'</tr>'for r in runs)
 (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Sabah Rail run report</title><style>body{font:16px system-ui;margin:35px;color:#14253b}table{border-collapse:collapse;width:100%}th,td{padding:12px;text-align:left;border-bottom:1px solid #ddd}h1{color:#245c91}.notice{padding:16px;background:#fff6dc}</style><h1>Sabah Rail batch report</h1><p class="notice">'+status+'</p><p>One Python engine. See QA, run register and immutable JSON snapshots before interpreting results.</p><table><tr><th>Case</th><th>Year</th><th>Source trips/day</th><th>Freight t/year</th><th>Status</th></tr>'+body+'</table><p>Road, station OD and full-appraisal gaps remain unresolved; no generated missing values.</p>')
 manifest={'batch_id':str(uuid.uuid4()),'timestamp':datetime.now(timezone.utc).isoformat(),'model_version':runs[0]['model_version'],'git_sha':os.getenv('MODEL_GIT_SHA','work-test'),'data_sha256':e.data_version,'execution_environment':'GitHub Actions'if os.getenv('GITHUB_ACTIONS')=='true'else'ChatGPT Work test','github_run_id':os.getenv('GITHUB_RUN_ID'),'run_count':len(runs),'input_policy':policy,'synthetic':synthetic,'duration_seconds':time.perf_counter()-start,'max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'python':platform.python_version(),'warnings':'Approval/calibration gaps preserved','files':[]}
 for p in out.iterdir():
  if p.is_file():manifest['files'].append({'name':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
 write_json(out/'batch_manifest.json',manifest)
 (out/'QA_REPORT.md').write_text(f"# Batch QA\n\nStatus: {status}. {len(runs)} immutable runs generated.\n\nRun checks are in qa.csv. Baseline approval remains separate from arithmetic validation. Execution: {manifest['execution_environment']}. Runtime/memory/file hashes in batch_manifest.json.\n")
 if any(r['validation_status']=='FAILED'for r in runs):raise ValueError('Run failed validation; inspect encrypted diagnostic outputs')
 return manifest

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--data');parser.add_argument('--output',required=True);parser.add_argument('--request');parser.add_argument('--case',choices=['Low','Base','High'],default='Base');parser.add_argument('--year',type=int,default=2045);parser.add_argument('--headway',type=float,default=20);parser.add_argument('--compare',action='store_true');parser.add_argument('--synthetic',action='store_true');parser.add_argument('--input-policy',choices=['approved_only','explicit_draft'],default='approved_only');parser.add_argument('--geometry')
 a=parser.parse_args();scenario={'case':a.case,'year':a.year,'overrides':{'headway_min':a.headway}};policy=a.input_policy;data=a.data
 if a.request:
  target=Path(os.getenv('RUNNER_TEMP','/tmp'))/('sabah-input-'+str(uuid.uuid4())+'.json');m=materialize_request(a.request,target);scenario=m['scenario'];policy=m['input_policy'];data=target
 try:
  result=execute(data,a.output,scenario,a.compare,policy,a.synthetic,a.geometry)
  print(json.dumps({'status':'complete','run_count':result['run_count'],'synthetic':result['synthetic'],'duration_seconds':round(result['duration_seconds'],3)}))
 finally:
  if a.request:target.unlink(missing_ok=True)

if __name__=='__main__':main()
