#!/usr/bin/env python3
import json, requests
from pathlib import Path

bbox="116.02,5.85,116.23,6.13"
url=f"https://api.openaerialmap.org/meta?bbox={bbox}&limit=100"
r=requests.get(url,timeout=120,headers={"Accept":"application/json","User-Agent":"SabahRailImageryCheck/1.0"})
print("status",r.status_code)
print(r.text[:2000])
r.raise_for_status()
j=r.json()
Path("oam_sabah_results.json").write_text(json.dumps(j,indent=2),encoding="utf-8")

results=j.get("results",[])
print("RESULT_COUNT", len(results) if isinstance(results,list) else type(results).__name__)
if isinstance(results,list):
    rows=[]
    for x in results:
        props=x.get("properties",{}) if isinstance(x,dict) else {}
        gsd=props.get("gsd", x.get("gsd") if isinstance(x,dict) else None)
        acq=props.get("acquisition_start", props.get("acquisition_end", props.get("datetime")))
        title=props.get("title", x.get("title") if isinstance(x,dict) else None)
        platform=props.get("platform", props.get("platform_type", props.get("oam:platform_type")))
        urlv=None
        for key in ("download_url","url","tms","thumbnail"):
            if isinstance(x,dict) and x.get(key): urlv=x.get(key); break
            if props.get(key): urlv=props.get(key); break
        rows.append({"gsd":gsd,"acquisition":acq,"title":title,"platform":platform,"url":urlv,"raw":x})
    def k(z):
        try:return float(z["gsd"])
        except:return 999999
    rows.sort(key=k)
    for row in rows[:20]:
        print("CANDIDATE",json.dumps({k:v for k,v in row.items() if k!="raw"},default=str))
