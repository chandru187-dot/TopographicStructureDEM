"""Cloud/Work command entrypoint; accepts JSON scenario, persists run and export."""
import argparse
import json
from pathlib import Path
from railmodel.engine import Engine
from railmodel.store import Store

p=argparse.ArgumentParser();p.add_argument('--scenario',required=True);p.add_argument('--output',required=True)
a=p.parse_args();r=Engine().run(json.loads(Path(a.scenario).read_text()));Store().insert(r)
out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(r,indent=2,allow_nan=False))
print(json.dumps({'run_id':r['run_id'],'status':r['validation_status'],'output':str(out)}))
