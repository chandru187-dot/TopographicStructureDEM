"""Authenticated API + same-origin browser dashboard; never invokes another model."""
from dataclasses import asdict
from pathlib import Path
import csv
import io
import json
import os
import secrets
import threading
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from .engine import Engine, Scenario
from .store import Store

app=FastAPI(title='Sabah Rail Integrated Model',version='0.3.0')
lock=threading.Lock()


def authorised(authorization: str | None = Header(default=None)):
    token=os.getenv('MODEL_API_TOKEN')
    if not token:
        if os.getenv('MODEL_DEV_MODE')=='1':return
        raise HTTPException(503,'Service locked: configure authentication')
    if not authorization or not secrets.compare_digest(authorization,'Bearer '+token):
        raise HTTPException(401,'Authentication required')


class RunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    year:int=Field(default=2033,ge=2024,le=2063)
    case:str='Base'
    passenger_variant:str='summary_draft'
    freight_variant:str='v47_draft'
    freight_package:str='S2'
    option:int=Field(default=3,ge=1,le=7)
    opening_year:int=Field(default=2033,ge=2024,le=2063)
    overrides:dict[str,float]=Field(default_factory=dict)
    station_od:dict | None=None


def engine():return Engine()


@app.get('/health')
def health():return {'service':'sabah-rail','version':'0.3.0','ready':bool(os.getenv('MODEL_API_TOKEN') or os.getenv('MODEL_DEV_MODE')=='1')}


@app.get('/',response_class=HTMLResponse)
def dashboard():return (Path(__file__).parent/'web/index.html').read_text()


@app.get('/api/evidence',dependencies=[Depends(authorised)])
def evidence():
    e=engine()
    return {k:e.data[k] for k in ['governance_status','sources','unresolved','passenger_survey','freight_survey','screenline_counts','station_catchment_outputs','passenger_forecasts','historical_rail_freight','geography']}


@app.post('/api/runs',dependencies=[Depends(authorised)])
def run(request:RunRequest):
    try:
        result=engine().run(request.model_dump())
        with lock, Store() as store:store.insert(result)
        return {k:v for k,v in result.items() if k!='input_snapshot'}
    except ValueError as exc:raise HTTPException(422,str(exc))


@app.get('/api/runs',dependencies=[Depends(authorised)])
def list_runs():
    with lock, Store() as store:rows=store.list()
    return [{k:r[k] for k in ['run_id','timestamp','model_version','data_version','scenario','validation_status']} for r in rows]


@app.get('/api/runs/{run_id}',dependencies=[Depends(authorised)])
def get_run(run_id:str):
    try:
        with lock, Store() as store:r=store.get(run_id)
        return {k:v for k,v in r.items() if k!='input_snapshot'}
    except KeyError:raise HTTPException(404,'Run not found')


@app.get('/api/runs/{run_id}/export.json',dependencies=[Depends(authorised)])
def export_json(run_id:str):
    try:
        with lock, Store() as store:r=store.get(run_id)
    except KeyError:raise HTTPException(404,'Run not found')
    return Response(json.dumps(r,indent=2,allow_nan=False),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="run-{run_id}.json"'})


@app.get('/api/runs/{run_id}/freight.csv',dependencies=[Depends(authorised)])
def export_csv(run_id:str):
    r=get_run(run_id);out=io.StringIO();writer=csv.DictWriter(out,fieldnames=['origin','destination','tonnes_year','teu_year']);writer.writeheader();writer.writerows(r['freight']['od'])
    return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="freight-{run_id}.csv"'})


@app.post('/api/sensitivity',dependencies=[Depends(authorised)])
def sensitivity(request:RunRequest):
    results=[]
    for headway in [10,20,30]:
        req=request.model_dump();req['overrides']={**req['overrides'],'headway_min':headway}
        r=engine().run(req)
        with lock, Store() as store:store.insert(r)
        results.append({k:v for k,v in r.items() if k!='input_snapshot'})
    return results
