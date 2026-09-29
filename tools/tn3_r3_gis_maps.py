#!/usr/bin/env python3
"""
TN3 R3 GIS map package for the Sabah Rail feasibility study.

Creates:
1. TN3_R3_Study_Corridor_Existing_Infrastructure.png/.svg
2. TN3_R3_Traffic_Survey_Locations.png/.svg
3. TN3_R3_Freight_Market_Nodes.png/.svg
4. TN3_R3_GIS_Metadata.json

The proposed feasible alignment and corridor boundary come from the project
GeoJSON stored in this repository. Traffic survey coordinates are transcribed
from the current TN3 working document survey-location figure. OSM is used only
for contextual existing roads, railway, land uses, water and place labels.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import contextily as cx
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, Patch
import numpy as np
import osmnx as ox
from shapely.geometry import Point, shape

INPUT = Path("data/Sabah_Rail_Study_Area_and_Alignment.geojson")
OUT = Path("output/tn3_r3_gis")
OUT.mkdir(parents=True, exist_ok=True)

WGS84 = "EPSG:4326"
WEB = "EPSG:3857"

TC = [
    ("TC1", "Putatan Station Access", 5.893560, 116.048538),
    ("TC2", "Jalan Lintas", 5.918491, 116.059386),
    ("TC3", "Jalan Penampang Bypass", 5.920841, 116.077778),
    ("TC4", "Jalan Lintas 2", 5.947637, 116.090752),
    ("TC5", "Jalan Damai", 5.970321, 116.094295),
    ("TC6", "Jalan Tuaran Bypass / Inanam", 5.994371, 116.119736),
    ("TC7", "Jalan UMS Access", 6.033230, 116.124870),
    ("TC8", "Jalan Sepanggar Corridor", 6.063977, 116.149229),
    ("TC9", "KKIP South Access", 6.084468, 116.161702),
    ("TC10", "KKIP Main Gate", 6.088205, 116.194001),
    ("TC11", "Sepanggar Port Access", 6.074137, 116.129077),
    ("TC12", "Sapangar Bay Container Terminal", 6.086815, 116.132717),
    ("TC13", "Pan Borneo 1", 5.891604, 116.086553),
    ("TC14", "Pan Borneo 2", 5.925205, 116.128416),
    ("TC15", "Pan Borneo 3", 5.987978, 116.169676),
    ("TC16", "Jalan Kepayan", 5.949954, 116.059753),
    ("TC17", "Jalan Pintas", 5.959516, 116.066246),
    ("TC18", "Jalan Kolam", 5.964013, 116.083970),
    ("TC19", "Jalan Tun Fuad Stephen", 5.991446, 116.099783),
]

def halo():
    return [pe.withStroke(linewidth=3.0, foreground="white", alpha=0.96)]

def load_project():
    data = json.loads(INPUT.read_text(encoding="utf-8"))
    alignment = corridor = None
    nodes = {}
    for feat in data["features"]:
        name = str(feat.get("properties", {}).get("name", ""))
        geom = shape(feat["geometry"])
        low = name.lower()
        if geom.geom_type in ("LineString", "MultiLineString") and "feasible" in low:
            alignment = geom
        elif geom.geom_type in ("Polygon", "MultiPolygon") and "corridor" in low:
            corridor = geom
        elif geom.geom_type == "Point":
            nodes[name] = geom
    if alignment is None or corridor is None:
        raise RuntimeError("Project feasible alignment/corridor geometry not found.")
    nodes.setdefault("Putatan", Point(list(alignment.coords)[0]))
    return alignment, corridor, nodes

def expand(bounds, pad=0.025):
    minx, miny, maxx, maxy = bounds
    return (minx-pad, miny-pad, maxx+pad, maxy+pad)

def safe_osm(bbox):
    ox.settings.use_cache = True
    ox.settings.requests_timeout = 240
    ox.settings.overpass_rate_limit = True
    try:
        G = ox.graph.graph_from_bbox(
            bbox, network_type="drive_service", simplify=True,
            retain_all=True, truncate_by_edge=True,
        )
        roads = ox.convert.graph_to_gdfs(G, nodes=False, edges=True).reset_index(drop=True)
    except Exception as e:
        print("Road query warning:", e)
        roads = gpd.GeoDataFrame(geometry=[], crs=WGS84)

    tags = {
        "railway": ["rail", "station", "halt", "yard"],
        "landuse": ["industrial", "commercial", "retail", "residential"],
        "aeroway": ["aerodrome", "runway", "terminal"],
        "natural": ["water"],
        "waterway": ["river", "canal"],
        "place": ["city", "town", "suburb", "village"],
        "harbour": True,
        "man_made": ["pier", "breakwater"],
    }
    try:
        feats = ox.features.features_from_bbox(bbox, tags).reset_index(drop=False)
        if feats.crs is None:
            feats = feats.set_crs(WGS84)
    except Exception as e:
        print("Feature query warning:", e)
        feats = gpd.GeoDataFrame(geometry=[], crs=WGS84)
    return roads, feats

def norm(v):
    if isinstance(v, (list, tuple, set)):
        return str(next(iter(v), ""))
    return "" if v is None else str(v)

def road_class(v):
    h = norm(v)
    if h in {"motorway","motorway_link","trunk","trunk_link"}: return "Highway / trunk"
    if h in {"primary","primary_link"}: return "Primary road"
    if h in {"secondary","secondary_link"}: return "Secondary road"
    if h in {"tertiary","tertiary_link"}: return "Tertiary road"
    return "Local / service road"

def masks(feats):
    if len(feats) == 0:
        return {}
    def tag(col, vals):
        if col not in feats.columns: return np.zeros(len(feats), dtype=bool)
        vals=set(vals)
        return feats[col].apply(lambda x: norm(x) in vals).to_numpy()
    geom=feats.geometry.geom_type
    return {
        "rail": tag("railway", ["rail"]) & geom.isin(["LineString","MultiLineString"]).to_numpy(),
        "station": tag("railway", ["station","halt"]) & geom.isin(["Point"]).to_numpy(),
        "industrial": tag("landuse", ["industrial"]) & geom.isin(["Polygon","MultiPolygon"]).to_numpy(),
        "built": tag("landuse", ["commercial","retail","residential"]) & geom.isin(["Polygon","MultiPolygon"]).to_numpy(),
        "airport": tag("aeroway", ["aerodrome","terminal","runway"]) ,
        "water": (tag("natural", ["water"]) | tag("waterway", ["river","canal"])),
        "places": tag("place", ["city","town","suburb","village"]) & geom.isin(["Point"]).to_numpy(),
        "port": (
            (feats["harbour"].notna().to_numpy() if "harbour" in feats.columns else np.zeros(len(feats), dtype=bool))
            | tag("man_made", ["pier","breakwater"])
        ),
    }

def setup(ax, bounds_web):
    minx,miny,maxx,maxy=bounds_web
    dx=maxx-minx; dy=maxy-miny
    ax.set_xlim(minx-0.02*dx,maxx+0.02*dx)
    ax.set_ylim(miny-0.02*dy,maxy+0.02*dy)
    ax.set_axis_off()
    ax.set_facecolor("#f6f7f8")
    try:
        cx.add_basemap(
            ax, source=cx.providers.CartoDB.Positron,
            attribution=False, zoom="auto", crs=WEB, zorder=0
        )
    except Exception as e:
        print("Basemap warning:", e)

def add_north_scale(ax, length_km=5):
    ax.annotate("N", xy=(0.955,0.94), xytext=(0.955,0.855),
                xycoords="axes fraction", ha="center", va="center",
                fontsize=13, weight="bold",
                arrowprops=dict(arrowstyle="-|>", lw=1.7, color="#222"), zorder=50)
    x0,x1=ax.get_xlim(); y0,y1=ax.get_ylim()
    sx=x0+0.06*(x1-x0); sy=y0+0.055*(y1-y0); L=length_km*1000
    ax.plot([sx,sx+L],[sy,sy],lw=4,color="#222",zorder=50)
    ax.text(sx+L/2,sy+0.012*(y1-y0),f"{length_km} km",ha="center",fontsize=8.5,weight="bold",zorder=50)

def plot_context(ax, roads, feats, corridor_g, alignment_g, nodes_g, show_alignment=True):
    if len(roads):
        rr=roads.to_crs(WEB).copy()
        rr["map_class"]=rr["highway"].apply(road_class)
        styles={
            "Local / service road":("#c9cdd1",0.35,0.45),
            "Tertiary road":("#adb3b8",0.65,0.65),
            "Secondary road":("#879199",0.9,0.85),
            "Primary road":("#d09a54",1.2,0.95),
            "Highway / trunk":("#b56b16",1.8,1.0),
        }
        for k,(c,lw,a) in styles.items():
            s=rr[rr["map_class"]==k]
            if len(s): s.plot(ax=ax,color=c,linewidth=lw,alpha=a,zorder=5)

    if len(feats):
        f=feats.to_crs(WEB); m=masks(f)
        for key,color,edge,alpha,z in [
            ("built","#e4e7e9","none",0.28,2),
            ("industrial","#ead9b6","#c1a36b",0.55,3),
            ("water","#cfe8f5","#7aaed0",0.65,3),
            ("airport","#ddd7eb","#9285ad",0.5,4),
        ]:
            if key in m and np.any(m[key]):
                f.loc[m[key]].plot(ax=ax,facecolor=color,edgecolor=edge,linewidth=0.5,alpha=alpha,zorder=z)
        if "rail" in m and np.any(m["rail"]):
            rail=f.loc[m["rail"]]
            rail.plot(ax=ax,color="white",linewidth=4.5,zorder=12)
            rail.plot(ax=ax,color="#34383b",linewidth=2.0,zorder=13)
        if "station" in m and np.any(m["station"]):
            f.loc[m["station"]].plot(ax=ax,marker="o",facecolor="white",edgecolor="#34383b",markersize=28,zorder=14)

    corridor_g.boundary.plot(ax=ax,color="#486f91",linewidth=1.7,linestyle="--",alpha=0.9,zorder=18)
    if show_alignment:
        alignment_g.plot(ax=ax,color="white",linewidth=6.0,zorder=20)
        alignment_g.plot(ax=ax,color="#0b5fa5",linewidth=3.1,zorder=21)

    for name,pt in nodes_g.items():
        if name.lower().startswith(("sepangar","sapangar","kkip","putatan")):
            ax.scatter(pt.x,pt.y,s=55,marker="o",facecolor="white",edgecolor="#0b5fa5",linewidth=1.6,zorder=25)
            label = "Sapangar Port" if name.lower().startswith("sepangar") else name
            ax.annotate(label,(pt.x,pt.y),xytext=(6,6),textcoords="offset points",
                        fontsize=9,weight="bold",path_effects=halo(),zorder=26)

def save(fig, stem):
    fig.savefig(OUT/f"{stem}.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT/f"{stem}.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)

def corridor_map(roads, feats, corridor_g, alignment_g, nodes_g):
    fig,ax=plt.subplots(figsize=(11.69,8.27))
    setup(ax,corridor_g.total_bounds)
    plot_context(ax,roads,feats,corridor_g,alignment_g,nodes_g,True)
    add_north_scale(ax)
    ax.set_title("Sabah Rail Study Corridor and Existing Infrastructure",
                 loc="left",fontsize=16,weight="bold",pad=14)
    ax.text(0.0,1.005,"Putatan – Kota Kinabalu – KKIP – Sapangar Port | Feasible alignment 44.3 km",
            transform=ax.transAxes,ha="left",fontsize=9.5,color="#4c5660")
    legend=[
        Line2D([0],[0],color="#0b5fa5",lw=3,label="Proposed feasible alignment"),
        Line2D([0],[0],color="#34383b",lw=2,label="Existing mapped railway"),
        Line2D([0],[0],color="#b56b16",lw=2,label="Highway / trunk road"),
        Patch(facecolor="#ead9b6",edgecolor="#c1a36b",label="Industrial land use"),
        Line2D([0],[0],color="#486f91",lw=1.7,ls="--",label="Study corridor boundary"),
    ]
    ax.legend(handles=legend,loc="lower right",frameon=True,framealpha=0.95,fontsize=8.5)
    fig.text(0.01,0.012,
             "Sources: Project feasible alignment/corridor geometry; OpenStreetMap contextual infrastructure. "
             "Map is for TN3 feasibility reporting, not detailed engineering.",
             fontsize=7.5,color="#5b6268")
    save(fig,"TN3_R3_Study_Corridor_Existing_Infrastructure")

def traffic_map(roads, feats, corridor_g, alignment_g, nodes_g):
    fig,ax=plt.subplots(figsize=(11.69,8.27))
    setup(ax,corridor_g.total_bounds)
    plot_context(ax,roads,feats,corridor_g,alignment_g,nodes_g,True)
    t=gpd.GeoDataFrame(
        [{"id":i,"name":n,"geometry":Point(lon,lat)} for i,n,lat,lon in TC],crs=WGS84
    ).to_crs(WEB)
    t.plot(ax=ax,marker="o",facecolor="#f28c28",edgecolor="#532c0b",markersize=68,linewidth=1.1,zorder=35)
    for _,r in t.iterrows():
        ax.annotate(r["id"],(r.geometry.x,r.geometry.y),xytext=(4,4),textcoords="offset points",
                    fontsize=8.2,weight="bold",path_effects=halo(),zorder=36)
    add_north_scale(ax)
    ax.set_title("Traffic Count Survey Locations and Proposed Railway Corridor",
                 loc="left",fontsize=16,weight="bold",pad=14)
    ax.text(0.0,1.005,"TC1–TC19 | 6-hour classified screenline counts | AM 06:00–09:00 and PM 15:00–18:00",
            transform=ax.transAxes,ha="left",fontsize=9.4,color="#4c5660")
    ax.legend(handles=[
        Line2D([0],[0],marker="o",color="none",markerfacecolor="#f28c28",markeredgecolor="#532c0b",markersize=8,label="Traffic count location"),
        Line2D([0],[0],color="#0b5fa5",lw=3,label="Proposed feasible alignment"),
        Line2D([0],[0],color="#34383b",lw=2,label="Existing mapped railway"),
    ],loc="lower right",frameon=True,framealpha=0.95,fontsize=8.5)
    fig.text(0.01,0.012,
             "Survey coordinates transcribed from the TN3 working survey-location figure. "
             "Context layers: OpenStreetMap; project alignment: repository geometry.",
             fontsize=7.5,color="#5b6268")
    save(fig,"TN3_R3_Traffic_Survey_Locations")

def freight_map(roads, feats, corridor_g, alignment_g, nodes_g):
    fig,ax=plt.subplots(figsize=(11.69,8.27))
    setup(ax,corridor_g.total_bounds)
    plot_context(ax,roads,feats,corridor_g,alignment_g,nodes_g,True)

    # Conceptual freight market nodes: coordinates are map anchors, not terminal designs.
    p_put=nodes_g.get("Putatan")
    p_port=None; p_kkip=None
    for name,p in nodes_g.items():
        if name.lower().startswith(("sepangar","sapangar")): p_port=p
        if name.lower()=="kkip": p_kkip=p
    anchors={
        "N1\nSouthern JKNS\nHinterland": Point(p_put.x, p_put.y-9000),
        "N2\nPutatan / Lok Kawi /\nPenampang": Point(p_put.x+800, p_put.y+1800),
        "N3\nKK / Inanam /\nKolombong": gpd.GeoSeries([Point(116.119736,5.994371)],crs=WGS84).to_crs(WEB).iloc[0],
        "N4\nKKIP": p_kkip,
        "N5\nSapangar Port": p_port,
        "N6\nTelipok / Tuaran /\nNorthern Corridor": gpd.GeoSeries([Point(116.178,6.103)],crs=WGS84).to_crs(WEB).iloc[0],
    }
    for lab,p in anchors.items():
        ax.scatter(p.x,p.y,s=120,marker="o",facecolor="#ffffff",edgecolor="#234f72",linewidth=2.0,zorder=40)
        ax.annotate(lab,(p.x,p.y),xytext=(7,7),textcoords="offset points",fontsize=8.4,weight="bold",
                    path_effects=halo(),zorder=41)

    def arrow(a,b,rad=0.0,label=None):
        pa,pb=anchors[a],anchors[b]
        ar=FancyArrowPatch((pa.x,pa.y),(pb.x,pb.y),arrowstyle="<|-|>",mutation_scale=10,
                           linewidth=2.0,color="#8a4f1d",alpha=0.86,
                           connectionstyle=f"arc3,rad={rad}",zorder=32)
        ax.add_patch(ar)
        if label:
            mx=(pa.x+pb.x)/2; my=(pa.y+pb.y)/2
            ax.text(mx,my,label,fontsize=7.8,weight="semibold",ha="center",
                    path_effects=halo(),zorder=42)

    arrow("N5\nSapangar Port","N4\nKKIP",0.12,"Port ↔ KKIP")
    arrow("N5\nSapangar Port","N2\nPutatan / Lok Kawi /\nPenampang",-0.08,"Port ↔ Putatan")
    arrow("N5\nSapangar Port","N1\nSouthern JKNS\nHinterland",0.14,"Port ↔ South")
    arrow("N4\nKKIP","N1\nSouthern JKNS\nHinterland",-0.14,"KKIP ↔ South")

    add_north_scale(ax)
    ax.set_title("Conceptual Freight Market Nodes and Rail-Addressable Movements",
                 loc="left",fontsize=16,weight="bold",pad=14)
    ax.text(0.0,1.005,"Feasibility-stage market framework | arrows indicate market relationships, not observed OD volumes",
            transform=ax.transAxes,ha="left",fontsize=9.4,color="#4c5660")
    ax.legend(handles=[
        Line2D([0],[0],color="#0b5fa5",lw=3,label="Proposed feasible alignment"),
        Line2D([0],[0],color="#8a4f1d",lw=2,label="Principal freight market relationship"),
        Line2D([0],[0],marker="o",color="none",markerfacecolor="white",markeredgecolor="#234f72",markersize=9,label="Freight market node"),
    ],loc="lower right",frameon=True,framealpha=0.95,fontsize=8.5)
    fig.text(0.01,0.012,
             "Node locations are conceptual market anchors for TN3 demand assessment and do not represent confirmed terminal/siding locations. "
             "Underlying corridor geometry is the project feasible alignment.",
             fontsize=7.5,color="#5b6268")
    save(fig,"TN3_R3_Freight_Market_Nodes")

def main():
    alignment,corridor,nodes=load_project()
    base=gpd.GeoDataFrame(geometry=[corridor],crs=WGS84)
    bbox=expand(corridor.bounds,0.03)
    roads,feats=safe_osm(bbox)

    alignment_g=gpd.GeoDataFrame({"name":["Proposed Feasible Alignment"]},geometry=[alignment],crs=WGS84).to_crs(WEB)
    corridor_g=base.to_crs(WEB)
    nodes_g={k:gpd.GeoSeries([v],crs=WGS84).to_crs(WEB).iloc[0] for k,v in nodes.items()}

    corridor_map(roads,feats,corridor_g,alignment_g,nodes_g)
    traffic_map(roads,feats,corridor_g,alignment_g,nodes_g)
    freight_map(roads,feats,corridor_g,alignment_g,nodes_g)

    metadata={
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "project_geometry":"data/Sabah_Rail_Study_Area_and_Alignment.geojson",
        "project_alignment_name":"Proposed Feasible Alignment_ 44.3km",
        "traffic_locations":len(TC),
        "context_source":"OpenStreetMap via OSMnx; CartoDB Positron basemap when available",
        "outputs":[
            "TN3_R3_Study_Corridor_Existing_Infrastructure.png",
            "TN3_R3_Traffic_Survey_Locations.png",
            "TN3_R3_Freight_Market_Nodes.png",
        ],
        "notes":[
            "Traffic coordinates are from the current TN3 working survey-location figure.",
            "Freight node locations N1/N2/N3/N6 are conceptual market anchors, not terminal designs.",
            "Maps are for feasibility reporting, not detailed engineering or cadastral boundary use.",
        ],
    }
    (OUT/"TN3_R3_GIS_Metadata.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    print(json.dumps(metadata,indent=2))

if __name__=="__main__":
    main()
