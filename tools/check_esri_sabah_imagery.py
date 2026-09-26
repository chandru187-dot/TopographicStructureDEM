#!/usr/bin/env python3
import json, requests

points = {
    "Putatan_start": (116.0494, 5.8840),
    "Kota_Kinabalu_mid": (116.1100, 6.0058),
    "KKIP": (116.1585, 6.0876),
    "Sepanggar_Port": (116.1226, 6.0689),
}

urls = [
    "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/0/query",
    "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/identify",
]

def get(url, params):
    r=requests.get(url,params=params,timeout=60,headers={"User-Agent":"SabahRailEsriCheck/1.0"})
    print("\nURL",r.url)
    print("STATUS",r.status_code)
    print(r.text[:5000])
    return r

for name,(lon,lat) in points.items():
    print("\n====",name,lon,lat,"====")
    params={
        "f":"json",
        "where":"1=1",
        "geometry":f"{lon},{lat}",
        "geometryType":"esriGeometryPoint",
        "inSR":"4326",
        "outFields":"*",
        "returnGeometry":"false",
    }
    get(urls[0],params)

    # Identify fallback on root service if sublayer query is not supported.
    extent=f"{lon-0.01},{lat-0.01},{lon+0.01},{lat+0.01}"
    params2={
        "f":"json",
        "geometry":json.dumps({"x":lon,"y":lat,"spatialReference":{"wkid":4326}}),
        "geometryType":"esriGeometryPoint",
        "sr":"4326",
        "mapExtent":extent,
        "imageDisplay":"1200,800,96",
        "tolerance":"4",
        "returnGeometry":"false",
        "layers":"all",
    }
    get("https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/identify",params2)
