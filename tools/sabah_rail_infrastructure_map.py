#!/usr/bin/env python3
"""
Sabah Rail - Existing Infrastructure Presentation Map

Purpose
-------
Create a slide-ready existing-conditions infrastructure map for the Sabah Rail
study area. This is intentionally NOT a DEM/contour map and does NOT draw the
proposed feasible railway alignment.

Sources
-------
- Project study-area geometry: data/Sabah_Rail_Study_Area_and_Alignment.geojson
- Existing mapped infrastructure: OpenStreetMap via OSMnx/Overpass
- Light basemap: CARTO Positron (OpenStreetMap-derived)

Outputs
-------
- 4K 16:9 presentation PNG
- map-only 4K PNG
- PDF and SVG
- extracted infrastructure GeoJSON layers
- metadata JSON with extraction timestamp and counts
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import contextily as cx
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as shp_transform


PROJECT_CRS = "EPSG:4326"
MAP_CRS = "EPSG:32650"  # WGS 84 / UTM zone 50N


def text_halo():
    return [pe.withStroke(linewidth=3.0, foreground="white", alpha=0.95)]


def load_project_geometry(path: Path):
    fc = json.loads(path.read_text(encoding="utf-8"))
    corridor = None
    project_nodes = []
    alignment_start = None

    for feat in fc["features"]:
        props = feat.get("properties", {})
        name = str(props.get("name", ""))
        geom = shape(feat["geometry"])

        if geom.geom_type in ("Polygon", "MultiPolygon") and "corridor" in name.lower():
            corridor = geom
        elif geom.geom_type in ("LineString", "MultiLineString") and "feasible" in name.lower():
            if geom.geom_type == "LineString":
                alignment_start = Point(geom.coords[0])
            else:
                first = list(geom.geoms)[0]
                alignment_start = Point(first.coords[0])
        elif geom.geom_type == "Point":
            project_nodes.append((name, geom))

    if corridor is None:
        raise RuntimeError("Corridor Area polygon not found")

    if alignment_start is not None:
        project_nodes.insert(0, ("Putatan", alignment_start))

    return fc, corridor, project_nodes


def expand_bounds(bounds, pad_deg=0.035):
    minx, miny, maxx, maxy = bounds
    return (minx - pad_deg, miny - pad_deg, maxx + pad_deg, maxy + pad_deg)


def safe_features_from_bbox(bbox, tags):
    # OSMnx 2.1 bbox order: left, bottom, right, top
    return ox.features.features_from_bbox(bbox, tags)


def normalize_tag(v):
    if isinstance(v, (list, tuple, set)):
        return str(next(iter(v), ""))
    if v is None:
        return ""
    return str(v)


def road_class(v):
    h = normalize_tag(v)
    if h in {"motorway", "motorway_link", "trunk", "trunk_link"}:
        return "Highway / trunk"
    if h in {"primary", "primary_link"}:
        return "Primary road"
    if h in {"secondary", "secondary_link"}:
        return "Secondary road"
    if h in {"tertiary", "tertiary_link"}:
        return "Tertiary road"
    return "Local / service road"


def ensure_gdf(gdf, crs=PROJECT_CRS):
    if gdf is None or len(gdf) == 0:
        return gpd.GeoDataFrame(geometry=[], crs=crs)
    if gdf.crs is None:
        gdf = gdf.set_crs(crs)
    return gdf


def query_osm(bbox):
    ox.settings.requests_timeout = 240
    ox.settings.overpass_rate_limit = True
    ox.settings.log_console = True
    ox.settings.use_cache = True

    # Full drivable road network (including service roads) for existing-road context.
    G = ox.graph.graph_from_bbox(
        bbox,
        network_type="drive_service",
        simplify=True,
        retain_all=True,
        truncate_by_edge=True,
    )
    roads = ox.convert.graph_to_gdfs(G, nodes=False, edges=True)
    roads = roads.reset_index(drop=True)
    roads["map_class"] = roads["highway"].apply(road_class)

    tags = {
        "railway": ["rail", "station", "halt", "tram_stop", "yard"],
        "public_transport": ["station", "stop_position"],
        "aeroway": ["aerodrome", "runway", "taxiway", "terminal"],
        "landuse": ["industrial", "commercial", "retail", "residential"],
        "harbour": True,
        "man_made": ["pier", "breakwater"],
        "amenity": ["bus_station", "ferry_terminal"],
        "waterway": ["river", "canal", "stream"],
        "natural": ["water"],
        "power": ["line", "minor_line", "substation"],
        "place": ["city", "town", "suburb", "village"],
    }
    features = safe_features_from_bbox(bbox, tags).reset_index(drop=False)
    return ensure_gdf(roads), ensure_gdf(features)


def tag_mask(gdf, col, values=None, any_value=False):
    if col not in gdf.columns:
        return np.zeros(len(gdf), dtype=bool)
    s = gdf[col]
    if any_value:
        return s.notna().to_numpy()
    values = set(values or [])
    return s.apply(lambda v: normalize_tag(v) in values).to_numpy()


def geom_mask(gdf, kinds):
    if len(gdf) == 0:
        return np.zeros(0, dtype=bool)
    return gdf.geometry.geom_type.isin(kinds).to_numpy()


def subset(gdf, mask):
    if len(gdf) == 0:
        return gdf.copy()
    return gdf.loc[mask].copy()


def save_geojson(gdf, path):
    if len(gdf) == 0:
        # valid empty GeoJSON
        path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        return
    out = gdf.to_crs(PROJECT_CRS)
    out.to_file(path, driver="GeoJSON")


def label_points(ax, gdf, label_col="name", max_labels=18, fontsize=8, marker=None):
    if len(gdf) == 0 or label_col not in gdf.columns:
        return

    candidates = gdf[gdf[label_col].notna()].copy()
    candidates[label_col] = candidates[label_col].astype(str)
    candidates = candidates[candidates[label_col].str.strip() != ""]
    if len(candidates) == 0:
        return

    # Deduplicate names and prefer larger polygons / first point.
    candidates["_area"] = candidates.geometry.area.fillna(0)
    candidates = candidates.sort_values("_area", ascending=False)
    candidates = candidates.drop_duplicates(subset=[label_col]).head(max_labels)

    for _, row in candidates.iterrows():
        geom = row.geometry
        pt = geom.representative_point() if geom.geom_type != "Point" else geom
        if marker:
            ax.scatter([pt.x], [pt.y], s=marker.get("s", 20),
                       marker=marker.get("marker", "o"),
                       facecolor=marker.get("facecolor", "white"),
                       edgecolor=marker.get("edgecolor", "black"),
                       linewidth=marker.get("linewidth", 0.8),
                       zorder=marker.get("zorder", 20))
        ax.annotate(
            row[label_col],
            (pt.x, pt.y),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=fontsize,
            weight="semibold",
            zorder=30,
            path_effects=text_halo(),
        )


def label_major_roads(ax, roads, max_labels=12):
    if len(roads) == 0 or "name" not in roads.columns:
        return

    r = roads[roads["map_class"].isin(
        ["Highway / trunk", "Primary road", "Secondary road"]
    )].copy()
    r = r[r["name"].notna()]
    if len(r) == 0:
        return

    r["_name"] = r["name"].apply(normalize_tag)
    r = r[r["_name"].str.strip() != ""]
    r["_len"] = r.geometry.length

    totals = (
        r.groupby("_name")["_len"]
         .sum()
         .sort_values(ascending=False)
         .head(max_labels)
         .index
    )

    used = []
    for name in totals:
        rr = r[r["_name"] == name].sort_values("_len", ascending=False)
        if len(rr) == 0:
            continue
        geom = rr.iloc[0].geometry
        pt = geom.interpolate(0.5, normalized=True)
        # simple spacing check
        if any(pt.distance(p) < 1200 for p in used):
            continue
        used.append(pt)
        ax.text(
            pt.x, pt.y, name,
            fontsize=7.7,
            rotation=0,
            ha="center", va="center",
            zorder=25,
            path_effects=text_halo(),
        )


def add_scale_bar(ax, length_km=5):
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    x0 = xlim[0] + 0.065 * (xlim[1] - xlim[0])
    y0 = ylim[0] + 0.065 * (ylim[1] - ylim[0])
    length = length_km * 1000
    ax.plot([x0, x0 + length], [y0, y0], linewidth=4, color="black", zorder=50)
    ax.plot([x0, x0], [y0 - 100, y0 + 100], linewidth=1, color="black", zorder=50)
    ax.plot([x0 + length, x0 + length], [y0 - 100, y0 + 100], linewidth=1, color="black", zorder=50)
    ax.text(
        x0 + length / 2, y0 + 260, f"{length_km} km",
        ha="center", va="bottom", fontsize=8.5, weight="semibold",
        zorder=50, path_effects=text_halo()
    )


def add_north_arrow(ax):
    ax.annotate(
        "N",
        xy=(0.955, 0.935),
        xytext=(0.955, 0.845),
        xycoords="axes fraction",
        ha="center", va="center",
        fontsize=14, weight="bold",
        arrowprops=dict(arrowstyle="-|>", lw=1.8, color="black"),
        zorder=60,
    )


def render_map(
    out_png, out_pdf, out_svg, roads, features,
    corridor, project_nodes, extraction_date, titled=True
):
    roads = roads.to_crs(MAP_CRS)
    features = features.to_crs(MAP_CRS)
    corridor_gdf = gpd.GeoDataFrame(
        {"name": ["Sabah Rail Study Area"]}, geometry=[corridor], crs=PROJECT_CRS
    ).to_crs(MAP_CRS)

    # Split feature classes.
    water_poly = subset(features, tag_mask(features, "natural", ["water"]) & geom_mask(features, ["Polygon","MultiPolygon"]))
    waterways = subset(features, tag_mask(features, "waterway", ["river","canal","stream"]) & geom_mask(features, ["LineString","MultiLineString"]))
    industrial = subset(features, tag_mask(features, "landuse", ["industrial"]) & geom_mask(features, ["Polygon","MultiPolygon"]))
    builtup = subset(features, tag_mask(features, "landuse", ["residential","commercial","retail"]) & geom_mask(features, ["Polygon","MultiPolygon"]))

    railway_lines = subset(features, tag_mask(features, "railway", ["rail"]) & geom_mask(features, ["LineString","MultiLineString"]))
    railway_points = subset(features, (
        tag_mask(features, "railway", ["station","halt"]) |
        tag_mask(features, "public_transport", ["station"])
    ) & geom_mask(features, ["Point"]))

    airport_poly = subset(features, tag_mask(features, "aeroway", ["aerodrome","terminal"]) & geom_mask(features, ["Polygon","MultiPolygon"]))
    runways = subset(features, tag_mask(features, "aeroway", ["runway","taxiway"]) & geom_mask(features, ["LineString","MultiLineString","Polygon","MultiPolygon"]))

    port_feats = subset(features, (
        tag_mask(features, "harbour", any_value=True) |
        tag_mask(features, "man_made", ["pier","breakwater"]) |
        tag_mask(features, "amenity", ["ferry_terminal"])
    ))

    power_lines = subset(features, tag_mask(features, "power", ["line","minor_line"]) & geom_mask(features, ["LineString","MultiLineString"]))
    substations = subset(features, tag_mask(features, "power", ["substation"]))

    transport_points = subset(features, tag_mask(features, "amenity", ["bus_station","ferry_terminal"]) & geom_mask(features, ["Point"]))
    places = subset(features, tag_mask(features, "place", ["city","town","suburb","village"]) & geom_mask(features, ["Point"]))

    # Exact 16:9 canvas. 16x9 at 240 dpi => 3840x2160.
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes([0.035, 0.075, 0.77, 0.84] if titled else [0.025,0.04,0.81,0.92])

    # Balanced presentation extent based on the project corridor.
    # Add more east-west context so the long north-south corridor fills a 16:9 slide better.
    cxmin, cymin, cxmax, cymax = corridor_gdf.total_bounds
    ax.set_xlim(cxmin - 8500, cxmax + 8500)
    ax.set_ylim(cymin - 3000, cymax + 3000)

    # Clean light basemap.
    try:
        cx.add_basemap(
            ax,
            source=cx.providers.OpenStreetMap.Mapnik,
            crs=MAP_CRS,
            attribution=False,
            zoom="auto",
            alpha=0.52,
            reset_extent=True,
        )
    except Exception as exc:
        print("Basemap warning:", repr(exc))

    # Soft contextual fills.
    if len(builtup):
        builtup.plot(ax=ax, facecolor="#e8e8e8", edgecolor="none", alpha=0.32, zorder=2)
    if len(water_poly):
        water_poly.plot(ax=ax, facecolor="#cfe8f3", edgecolor="none", alpha=0.75, zorder=3)
    if len(industrial):
        industrial.plot(ax=ax, facecolor="#e9d7ae", edgecolor="#b89a61", linewidth=0.5, alpha=0.60, zorder=4)
    if len(airport_poly):
        airport_poly.plot(ax=ax, facecolor="#ddd6ee", edgecolor="#9687ba", linewidth=0.8, alpha=0.55, zorder=5)
    if len(runways):
        runways.plot(ax=ax, color="#80758e", linewidth=2.0, alpha=0.75, zorder=8)
    if len(waterways):
        waterways.plot(ax=ax, color="#6aaed6", linewidth=0.9, alpha=0.8, zorder=8)

    # Roads by hierarchy. Local roads provide network texture, major roads read clearly.
    road_style = {
        "Local / service road": ("#bfc3c7", 0.45, 0.55),
        "Tertiary road": ("#9ea4aa", 0.8, 0.75),
        "Secondary road": ("#6f7880", 1.15, 0.9),
        "Primary road": ("#d28b38", 1.65, 0.98),
        "Highway / trunk": ("#b45f06", 2.35, 1.0),
    }
    for klass in ["Local / service road","Tertiary road","Secondary road","Primary road","Highway / trunk"]:
        rr = roads[roads["map_class"] == klass]
        if len(rr):
            color, lw, alpha = road_style[klass]
            rr.plot(ax=ax, color=color, linewidth=lw, alpha=alpha, zorder=10)

    # Existing railway gets strong visual priority.
    if len(railway_lines):
        railway_lines.plot(ax=ax, color="white", linewidth=4.0, alpha=0.95, zorder=16)
        railway_lines.plot(ax=ax, color="#202124", linewidth=2.0, alpha=1.0, zorder=17)

    if len(power_lines):
        power_lines.plot(ax=ax, color="#8a6bb8", linewidth=0.8, alpha=0.65, linestyle="--", zorder=12)

    if len(port_feats):
        line_ports = port_feats[port_feats.geometry.geom_type.isin(["LineString","MultiLineString"])]
        poly_ports = port_feats[port_feats.geometry.geom_type.isin(["Polygon","MultiPolygon"])]
        point_ports = port_feats[port_feats.geometry.geom_type.isin(["Point"])]
        if len(poly_ports):
            poly_ports.plot(ax=ax, facecolor="#d7e6f5", edgecolor="#4b83b6", linewidth=0.8, alpha=0.65, zorder=9)
        if len(line_ports):
            line_ports.plot(ax=ax, color="#4b83b6", linewidth=1.3, zorder=14)
        if len(point_ports):
            point_ports.plot(ax=ax, color="#245c8a", markersize=22, zorder=20)

    if len(substations):
        substations.plot(ax=ax, facecolor="#f0e8f7", edgecolor="#7a5aa6", linewidth=0.8, markersize=18, zorder=20)

    # Study area boundary only; no proposed railway alignment.
    corridor_gdf.plot(
        ax=ax,
        facecolor="#2b6cb0",
        edgecolor="#1f5f99",
        linewidth=2.4,
        alpha=0.055,
        linestyle="--",
        zorder=18,
    )

    # Railway stations, other transport points.
    if len(railway_points):
        railway_points.plot(
            ax=ax, marker="o", facecolor="white", edgecolor="#202124",
            markersize=34, linewidth=1.2, zorder=22
        )
    if len(transport_points):
        transport_points.plot(
            ax=ax, marker="s", facecolor="white", edgecolor="#426b8a",
            markersize=28, linewidth=1.0, zorder=22
        )

    # Project reference nodes: place context, not proposed alignment.
    tx = Transformer.from_crs(PROJECT_CRS, MAP_CRS, always_xy=True)
    for name, p in project_nodes:
        pp = shp_transform(tx.transform, p)
        ax.scatter([pp.x], [pp.y], s=50, facecolor="white",
                   edgecolor="#1f5f99", linewidth=1.6, zorder=27)
        ax.annotate(
            name,
            (pp.x, pp.y),
            xytext=(6, 6), textcoords="offset points",
            fontsize=9.5, weight="bold", zorder=31,
            path_effects=text_halo(),
        )

    # Key infrastructure labels.
    label_major_roads(ax, roads, max_labels=7)
    label_points(ax, railway_points, max_labels=5, fontsize=8)
    label_points(ax, airport_poly, max_labels=4, fontsize=9)
    # Industrial and port polygons are symbolized but not mass-labelled to avoid clutter.
    # KKIP and Sepanggar Port are already labelled from the verified project reference nodes.

    # Place names are left to the OSM basemap; avoid duplicate text on the presentation layer.

    add_scale_bar(ax, 5)
    add_north_arrow(ax)

    ax.set_axis_off()

    # Right-side legend/info panel.
    panel = fig.add_axes([0.825, 0.10, 0.155, 0.79])
    panel.axis("off")

    if titled:
        fig.text(
            0.035, 0.955,
            "SABAH RAIL STUDY — EXISTING INFRASTRUCTURE",
            fontsize=22, weight="bold", ha="left", va="top"
        )
        fig.text(
            0.035, 0.922,
            "Putatan • Kota Kinabalu • KKIP • Sepanggar Port",
            fontsize=11.5, ha="left", va="top"
        )

    panel.text(0.02, 0.97, "MAP LEGEND", fontsize=11.5, weight="bold", va="top")
    handles = [
        Line2D([0],[0], color="#b45f06", lw=3, label="Highway / trunk"),
        Line2D([0],[0], color="#d28b38", lw=2, label="Primary road"),
        Line2D([0],[0], color="#6f7880", lw=1.5, label="Secondary road"),
        Line2D([0],[0], color="#202124", lw=2.5, label="Existing railway"),
        Line2D([0],[0], color="#6aaed6", lw=1.5, label="Major waterway"),
        Line2D([0],[0], color="#8a6bb8", lw=1, ls="--", label="Power line"),
        Patch(facecolor="#e9d7ae", edgecolor="#b89a61", label="Industrial area"),
        Patch(facecolor="#ddd6ee", edgecolor="#9687ba", label="Airport / aviation"),
        Patch(facecolor="#d7e6f5", edgecolor="#4b83b6", label="Port infrastructure"),
        Patch(facecolor="#2b6cb0", edgecolor="#1f5f99", alpha=0.12, label="Study area"),
    ]
    panel.legend(
        handles=handles, loc="upper left",
        bbox_to_anchor=(0.0, 0.90),
        frameon=False, fontsize=8.7,
        borderaxespad=0.0, handlelength=2.8, labelspacing=0.9,
    )

    panel.text(
        0.02, 0.37,
        "EXISTING CONDITIONS",
        fontsize=10.5, weight="bold", va="top"
    )
    panel.text(
        0.02, 0.335,
        "Mapped infrastructure includes:\n"
        "• Drivable road network\n"
        "• Existing railway + stations\n"
        "• Airport + runways\n"
        "• Port / pier facilities\n"
        "• Industrial areas\n"
        "• Rivers / waterways\n"
        "• Power infrastructure where mapped",
        fontsize=8.3, va="top", linespacing=1.45
    )

    panel.text(
        0.02, 0.12,
        "DATA NOTE",
        fontsize=9.5, weight="bold", va="top"
    )
    panel.text(
        0.02, 0.085,
        f"OpenStreetMap extraction: {extraction_date}\n"
        "Mapped features are subject to OSM completeness.\n"
        "Project study boundary from supplied Sabah Rail GIS data.",
        fontsize=7.4, va="top", linespacing=1.35
    )

    fig.text(
        0.035, 0.018,
        "Sources: © OpenStreetMap contributors • CARTO basemap • Sabah Rail project study-area GIS. "
        "Existing-infrastructure presentation map; proposed alignment intentionally not shown.",
        fontsize=7.1, ha="left"
    )

    fig.savefig(out_png, dpi=240, facecolor="white")
    if out_pdf:
        fig.savefig(out_pdf, facecolor="white")
    if out_svg:
        fig.savefig(out_svg, facecolor="white")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/Sabah_Rail_Study_Area_and_Alignment.geojson")
    ap.add_argument("--output", default="output/sabah_rail_infrastructure")
    ap.add_argument("--pad-deg", type=float, default=0.035)
    args = ap.parse_args()

    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)

    fc, corridor, project_nodes = load_project_geometry(Path(args.input))
    bbox = expand_bounds(corridor.bounds, args.pad_deg)

    roads, features = query_osm(bbox)

    # Persist the raw extracted layers for checking/reuse.
    save_geojson(roads, outdir / "Existing_Road_Network_OSM.geojson")

    railmask = tag_mask(features, "railway", ["rail","station","halt","yard"]) | tag_mask(features, "public_transport", ["station"])
    save_geojson(subset(features, railmask), outdir / "Existing_Railway_and_Stations_OSM.geojson")

    inframask = (
        tag_mask(features, "aeroway", ["aerodrome","runway","taxiway","terminal"]) |
        tag_mask(features, "landuse", ["industrial"]) |
        tag_mask(features, "harbour", any_value=True) |
        tag_mask(features, "man_made", ["pier","breakwater"]) |
        tag_mask(features, "amenity", ["bus_station","ferry_terminal"]) |
        tag_mask(features, "power", ["line","minor_line","substation"]) |
        tag_mask(features, "waterway", ["river","canal","stream"]) |
        tag_mask(features, "natural", ["water"])
    )
    save_geojson(subset(features, inframask), outdir / "Other_Existing_Infrastructure_OSM.geojson")

    extract_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    render_map(
        outdir / "Sabah_Rail_Existing_Infrastructure_Slide_4K.png",
        outdir / "Sabah_Rail_Existing_Infrastructure_Map.pdf",
        outdir / "Sabah_Rail_Existing_Infrastructure_Map.svg",
        roads, features, corridor, project_nodes, extract_date, titled=True
    )

    render_map(
        outdir / "Sabah_Rail_Existing_Infrastructure_MapOnly_4K.png",
        None, None,
        roads, features, corridor, project_nodes, extract_date, titled=False
    )

    counts = {
        "road_edges": int(len(roads)),
        "road_class_counts": {
            str(k): int(v) for k, v in roads["map_class"].value_counts().to_dict().items()
        },
        "railway_features": int(railmask.sum()),
        "other_infrastructure_features": int(inframask.sum()),
        "all_osm_feature_rows": int(len(features)),
    }

    metadata = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "map_title": "Sabah Rail Study - Existing Infrastructure",
        "osm_extraction_date_utc": extract_date,
        "query_bbox_wgs84": list(map(float, bbox)),
        "study_corridor_bounds_wgs84": list(map(float, corridor.bounds)),
        "map_crs": MAP_CRS,
        "proposed_alignment_shown": False,
        "source_notes": [
            "Existing infrastructure is based on OpenStreetMap features available at extraction time.",
            "The road network uses OSMnx drive_service network retrieval.",
            "OpenStreetMap Mapnik tiles are used as a subdued presentation basemap.",
            "The Sabah Rail study boundary comes from the project-supplied GeoJSON.",
            "OSM is not an authoritative asset register; unmapped/private/new infrastructure may be absent.",
        ],
        "counts": counts,
        "outputs": [
            "Sabah_Rail_Existing_Infrastructure_Slide_4K.png",
            "Sabah_Rail_Existing_Infrastructure_MapOnly_4K.png",
            "Sabah_Rail_Existing_Infrastructure_Map.pdf",
            "Sabah_Rail_Existing_Infrastructure_Map.svg",
            "Existing_Road_Network_OSM.geojson",
            "Existing_Railway_and_Stations_OSM.geojson",
            "Other_Existing_Infrastructure_OSM.geojson",
        ],
    }
    (outdir / "Sabah_Rail_Existing_Infrastructure_Metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
