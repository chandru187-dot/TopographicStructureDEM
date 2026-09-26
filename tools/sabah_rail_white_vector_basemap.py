#!/usr/bin/env python3
"""
Create a clean, zoomable white-background vector basemap for the Sabah Rail study area.

Purpose:
- Presentation background only.
- Existing roads, buildings, existing railway, waterways, airport/runway,
  port and industrial fabric.
- No proposed railway alignment.
- No invented cadastral parcel lines. Official cadastral parcels can be
  added later as a separate overlay when an authoritative JTU dataset is available.

Outputs:
- SVG vector map (best for deep zoom / editing)
- PDF vector map
- 8K and 4K PNGs
- Extracted road/infrastructure GeoJSONs; buildings are embedded in the vector map
"""

from __future__ import annotations
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import shape, box
from shapely.ops import transform as shp_transform

PROJECT_CRS = "EPSG:4326"
MAP_CRS = "EPSG:32650"


def load_corridor(path: Path):
    fc = json.loads(path.read_text(encoding="utf-8"))
    corridor = None
    for f in fc.get("features", []):
        name = str(f.get("properties", {}).get("name", ""))
        geom = shape(f["geometry"])
        if geom.geom_type in ("Polygon", "MultiPolygon") and "corridor" in name.lower():
            corridor = geom
            break
    if corridor is None:
        raise RuntimeError("Corridor Area polygon not found")
    return corridor


def tag_value(v):
    if isinstance(v, (list, tuple, set)):
        return str(next(iter(v), ""))
    return "" if v is None else str(v)


def road_class(v):
    h = tag_value(v)
    if h in {"motorway","motorway_link","trunk","trunk_link"}:
        return "highway"
    if h in {"primary","primary_link"}:
        return "primary"
    if h in {"secondary","secondary_link"}:
        return "secondary"
    if h in {"tertiary","tertiary_link"}:
        return "tertiary"
    return "local"


def expand_bbox(bounds, pad_deg=0.025):
    minx, miny, maxx, maxy = bounds
    return (minx-pad_deg, miny-pad_deg, maxx+pad_deg, maxy+pad_deg)


def safe_features_from_bbox(bbox, tags):
    try:
        g = ox.features.features_from_bbox(bbox, tags)
        return g.reset_index(drop=False)
    except Exception as exc:
        print("Feature query failed for", bbox, tags, repr(exc))
        return gpd.GeoDataFrame(geometry=[], crs=PROJECT_CRS)


def query_buildings_polygon(poly_wgs84):
    try:
        print("Querying detailed building footprints inside buffered study corridor...")
        g = ox.features.features_from_polygon(poly_wgs84, {"building": True})
        return g.reset_index(drop=False)
    except Exception as exc:
        print("Building polygon query failed:", repr(exc))
        return gpd.GeoDataFrame(geometry=[], crs=PROJECT_CRS)


def query_osm(bbox, building_polygon):
    ox.settings.requests_timeout = 240
    ox.settings.overpass_rate_limit = True
    ox.settings.log_console = True
    ox.settings.use_cache = True

    G = ox.graph.graph_from_bbox(
        bbox,
        network_type="drive_service",
        simplify=True,
        retain_all=True,
        truncate_by_edge=True,
    )
    roads = ox.convert.graph_to_gdfs(G, nodes=False, edges=True).reset_index(drop=True)
    roads["map_class"] = roads["highway"].apply(road_class)

    buildings = query_buildings_polygon(building_polygon)

    tags = {
        "railway": ["rail","station","halt","yard"],
        "waterway": ["river","canal","stream","drain"],
        "natural": ["water","coastline"],
        "aeroway": ["aerodrome","runway","taxiway","terminal"],
        "landuse": ["industrial","commercial","retail"],
        "harbour": True,
        "man_made": ["pier","breakwater"],
    }
    infra = safe_features_from_bbox(bbox, tags)
    return roads, buildings, infra


def mask_tag(gdf, col, values=None, any_value=False):
    if col not in gdf.columns:
        return np.zeros(len(gdf), dtype=bool)
    if any_value:
        return gdf[col].notna().to_numpy()
    vals=set(values or [])
    return gdf[col].apply(lambda v: tag_value(v) in vals).to_numpy()


def subset(gdf, mask):
    return gdf.loc[mask].copy() if len(gdf) else gdf.copy()


def save_geojson(gdf, path):
    if len(gdf)==0:
        path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    else:
        gdf.to_crs(PROJECT_CRS).to_file(path, driver="GeoJSON")


def make_map(out_base: Path, roads, buildings, infra, corridor, dpi):
    roads = roads.to_crs(MAP_CRS)
    buildings = buildings.to_crs(MAP_CRS) if len(buildings) else buildings.set_crs(PROJECT_CRS).to_crs(MAP_CRS)
    infra = infra.to_crs(MAP_CRS) if len(infra) else infra.set_crs(PROJECT_CRS).to_crs(MAP_CRS)

    tx = Transformer.from_crs(PROJECT_CRS, MAP_CRS, always_xy=True)
    corridor_u = shp_transform(tx.transform, corridor)

    # Extent: corridor plus ~3 km context.
    ctx = corridor_u.buffer(3000)
    xmin,ymin,xmax,ymax = ctx.bounds

    fig = plt.figure(figsize=(16,9))
    ax = fig.add_axes([0,0,1,1])
    ax.set_facecolor("#fbfbf9")
    ax.set_xlim(xmin,xmax)
    ax.set_ylim(ymin,ymax)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    # Water first.
    water_poly = subset(infra, mask_tag(infra,"natural",["water"]) & infra.geometry.geom_type.isin(["Polygon","MultiPolygon"]).to_numpy())
    waterways = subset(infra, mask_tag(infra,"waterway",["river","canal","stream","drain"]) & infra.geometry.geom_type.isin(["LineString","MultiLineString"]).to_numpy())
    if len(water_poly):
        water_poly.plot(ax=ax, facecolor="#e9f2f6", edgecolor="#c2dbe7", linewidth=0.25, zorder=1)
    if len(waterways):
        waterways.plot(ax=ax, color="#bdd9e6", linewidth=0.45, alpha=0.9, zorder=2)

    # Building figure-ground.
    if len(buildings):
        bpoly = buildings[buildings.geometry.geom_type.isin(["Polygon","MultiPolygon"])]
        if len(bpoly):
            bpoly.plot(ax=ax, facecolor="#d7d7d5", edgecolor="#c5c5c2", linewidth=0.10, alpha=0.93, zorder=4)

    # Industrial / airport / port forms.
    industrial = subset(infra, mask_tag(infra,"landuse",["industrial","commercial","retail"]) & infra.geometry.geom_type.isin(["Polygon","MultiPolygon"]).to_numpy())
    if len(industrial):
        industrial.plot(ax=ax, facecolor="#eceae5", edgecolor="#cfcac0", linewidth=0.30, alpha=0.65, zorder=3)

    airport = subset(infra, mask_tag(infra,"aeroway",["aerodrome","terminal"]) & infra.geometry.geom_type.isin(["Polygon","MultiPolygon"]).to_numpy())
    runways = subset(infra, mask_tag(infra,"aeroway",["runway","taxiway"]))
    if len(airport):
        airport.plot(ax=ax, facecolor="#efefef", edgecolor="#c8c8c8", linewidth=0.35, alpha=0.8, zorder=5)
    if len(runways):
        runways.plot(ax=ax, color="#9e9e9e", linewidth=1.8, alpha=0.85, zorder=8)

    port = subset(infra, mask_tag(infra,"harbour",any_value=True) | mask_tag(infra,"man_made",["pier","breakwater"]))
    if len(port):
        poly = port[port.geometry.geom_type.isin(["Polygon","MultiPolygon"])]
        line = port[port.geometry.geom_type.isin(["LineString","MultiLineString"])]
        if len(poly):
            poly.plot(ax=ax, facecolor="#e7ebed", edgecolor="#aeb7bb", linewidth=0.35, zorder=6)
        if len(line):
            line.plot(ax=ax, color="#90999d", linewidth=0.65, zorder=7)

    # Roads, carefully weighted.
    styles = {
        "local":     ("#d1d1cf", 0.22, 0.85),
        "tertiary":  ("#bbbbba", 0.38, 0.92),
        "secondary": ("#999998", 0.62, 0.95),
        "primary":   ("#777776", 0.95, 1.0),
        "highway":   ("#555554", 1.35, 1.0),
    }
    for cls in ["local","tertiary","secondary","primary","highway"]:
        r = roads[roads["map_class"]==cls]
        if len(r):
            col,lw,a = styles[cls]
            r.plot(ax=ax, color=col, linewidth=lw, alpha=a, zorder=10)

    # Existing railway, with white casing for legibility over dense buildings.
    rail = subset(infra, mask_tag(infra,"railway",["rail"]) & infra.geometry.geom_type.isin(["LineString","MultiLineString"]).to_numpy())
    if len(rail):
        rail.plot(ax=ax, color="#fbfbf9", linewidth=2.2, zorder=12)
        rail.plot(ax=ax, color="#414141", linewidth=0.9, zorder=13)

    # Do not draw the proposed rail alignment or study boundary.
    # This map is intended solely as a clean background.

    fig.savefig(out_base.with_suffix(".svg"), facecolor="#fbfbf9", bbox_inches="tight", pad_inches=0)
    fig.savefig(out_base.with_suffix(".pdf"), facecolor="#fbfbf9", bbox_inches="tight", pad_inches=0)
    fig.savefig(out_base.parent / (out_base.name+"_8K.png"), dpi=480, facecolor="#fbfbf9", bbox_inches="tight", pad_inches=0)
    fig.savefig(out_base.parent / (out_base.name+"_4K.png"), dpi=240, facecolor="#fbfbf9", bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    return {
        "roads": int(len(roads)),
        "buildings": int(len(buildings)),
        "rail_features": int(len(rail)),
        "waterway_features": int(len(waterways)),
        "industrial_features": int(len(industrial)),
        "airport_features": int(len(airport)+len(runways)),
        "port_features": int(len(port)),
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input", default="data/Sabah_Rail_Study_Area_and_Alignment.geojson")
    ap.add_argument("--output", default="output/sabah_rail_white_vector_basemap")
    args=ap.parse_args()

    outdir=Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)

    corridor=load_corridor(Path(args.input))

    # Detailed building footprints only where presentation zooming matters:
    # the actual study corridor plus 1.5 km context.
    to_utm=Transformer.from_crs(PROJECT_CRS, MAP_CRS, always_xy=True)
    to_wgs=Transformer.from_crs(MAP_CRS, PROJECT_CRS, always_xy=True)
    corridor_u=shp_transform(to_utm.transform, corridor)
    building_context=shp_transform(to_wgs.transform, corridor_u.buffer(1500))

    # Roads/infrastructure keep slightly wider context for map continuity.
    bbox=expand_bbox(corridor.bounds, 0.02)
    roads,buildings,infra=query_osm(bbox, building_context)

    save_geojson(roads, outdir/"Existing_Roads_OSM.geojson")
    save_geojson(infra, outdir/"Existing_Infrastructure_OSM.geojson")

    counts=make_map(
        outdir/"Sabah_Rail_White_Vector_Basemap",
        roads,buildings,infra,corridor,dpi=480
    )

    meta={
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "map_purpose": "White-background existing-conditions presentation basemap",
        "proposed_alignment_shown": False,
        "study_boundary_shown": False,
        "crs": MAP_CRS,
        "sources": {
            "roads_buildings_infrastructure": "OpenStreetMap via OSMnx/Overpass",
            "study_extent": "Sabah Rail project Corridor Area",
        },
        "cadastral_status": (
            "No authoritative cadastral parcel dataset was available in the project files. "
            "Parcel boundaries have therefore NOT been invented. An official JTU Sabah parcel "
            "SHP/KML can be added later as a separate thin-line overlay."
        ),
        "feature_counts": counts,
        "outputs": [
            "Sabah_Rail_White_Vector_Basemap.svg",
            "Sabah_Rail_White_Vector_Basemap.pdf",
            "Sabah_Rail_White_Vector_Basemap_8K.png",
            "Sabah_Rail_White_Vector_Basemap_4K.png",
            "Existing_Roads_OSM.geojson",
            "Existing_Infrastructure_OSM.geojson",
        ],
    }
    (outdir/"Sabah_Rail_White_Vector_Basemap_Metadata.json").write_text(
        json.dumps(meta,indent=2),encoding="utf-8"
    )
    print(json.dumps(meta,indent=2))


if __name__=="__main__":
    main()
