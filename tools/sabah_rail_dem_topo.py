#!/usr/bin/env python3
"""
Generate DEM/topographic deliverables for the Sabah Rail study corridor.

Study geometry:
  data/Sabah_Rail_Study_Area_and_Alignment.geojson
  - Proposed Feasible Alignment_ 44.3km
  - Corridor Area polygon
  - KKIP and Sepanggar Port points

Terrain:
  Copernicus DEM GLO-30 Public (2021 release), AWS Open Data.
  This is a DSM (includes surface objects) and is intended here for
  feasibility-level terrain assessment, not survey-grade final design.

Outputs include:
  - topographic PNG map
  - WGS84 and UTM Zone 50N GeoTIFF DEMs
  - 10 m contour GeoJSON
  - UTM XYZ point file
  - source/stats metadata JSON
"""

from __future__ import annotations
import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import rasterio
from rasterio.io import MemoryFile
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.warp import calculate_default_transform, reproject, Resampling
from pyproj import Transformer
from shapely.geometry import shape, mapping, LineString, MultiLineString
from shapely.ops import transform as shp_transform

COP30_ROOT = "https://copernicus-dem-30m.s3.amazonaws.com"
TARGET_CRS = "EPSG:32650"  # WGS 84 / UTM zone 50N


def tile_id(lat_deg: int, lon_deg: int) -> str:
    ns = "N" if lat_deg >= 0 else "S"
    ew = "E" if lon_deg >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_deg):02d}_00_{ew}{abs(lon_deg):03d}_00_DEM"


def tile_url(lat_deg: int, lon_deg: int) -> str:
    tid = tile_id(lat_deg, lon_deg)
    return f"{COP30_ROOT}/{tid}/{tid}.tif"


def download(url: str, dest: Path, session: requests.Session):
    if dest.exists() and dest.stat().st_size > 1000:
        return
    with session.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def load_project_geojson(path: Path):
    fc = json.loads(path.read_text(encoding="utf-8"))
    corridor = alignment = None
    nodes = []
    for feat in fc["features"]:
        name = str(feat.get("properties", {}).get("name", ""))
        geom = shape(feat["geometry"])
        if geom.geom_type in ("Polygon", "MultiPolygon") and "corridor" in name.lower():
            corridor = geom
        elif geom.geom_type in ("LineString", "MultiLineString") and "feasible" in name.lower():
            alignment = geom
        elif geom.geom_type == "Point":
            nodes.append((name, geom))
    if corridor is None:
        raise RuntimeError("Corridor Area polygon not found in project GeoJSON")
    if alignment is None:
        raise RuntimeError("Proposed Feasible Alignment not found in project GeoJSON")
    return fc, corridor, alignment, nodes


def needed_tiles(bounds):
    minx, miny, maxx, maxy = bounds
    lon0, lon1 = math.floor(minx), math.floor(maxx)
    lat0, lat1 = math.floor(miny), math.floor(maxy)
    return [(lat, lon) for lat in range(lat0, lat1 + 1)
                       for lon in range(lon0, lon1 + 1)]


def mosaic_and_clip(tile_paths, corridor):
    srcs = [rasterio.open(p) for p in tile_paths]
    try:
        mosaic, trans = merge(srcs)
        crs = srcs[0].crs
        nodata = srcs[0].nodata
        if nodata is None:
            nodata = -9999.0
        profile = srcs[0].profile.copy()
        with MemoryFile() as mem:
            with mem.open(
                driver="GTiff",
                height=mosaic.shape[1],
                width=mosaic.shape[2],
                count=1,
                dtype=mosaic.dtype,
                crs=crs,
                transform=trans,
                nodata=nodata,
            ) as ds:
                ds.write(mosaic[0], 1)
                clipped, clip_trans = mask(
                    ds, [mapping(corridor)], crop=True, filled=True, nodata=nodata
                )
        profile.update(
            driver="GTiff",
            height=clipped.shape[1],
            width=clipped.shape[2],
            count=1,
            transform=clip_trans,
            crs=crs,
            nodata=nodata,
            dtype=str(clipped.dtype),
            compress="deflate",
            tiled=True,
        )
        return clipped[0], clip_trans, crs, nodata, profile
    finally:
        for s in srcs:
            s.close()


def write_dem(path, arr, profile):
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr, 1)


def reproject_dem(src_path: Path, dst_path: Path, dst_crs=TARGET_CRS):
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )
        profile = src.profile.copy()
        profile.update(
            crs=dst_crs,
            transform=transform,
            width=width,
            height=height,
            compress="deflate",
            tiled=True,
        )
        with rasterio.open(dst_path, "w", **profile) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=transform,
                dst_crs=dst_crs,
                dst_nodata=src.nodata,
                resampling=Resampling.bilinear,
            )


def hillshade(arr, transform, nodata):
    z = arr.astype(np.float64)
    valid = np.isfinite(z) & (z != nodata)
    fill = float(np.nanmedian(z[valid])) if valid.any() else 0.0
    z2 = np.where(valid, z, fill)
    px = abs(transform.a)
    py = abs(transform.e)
    dy, dx = np.gradient(z2, py, px)
    slope = np.pi / 2.0 - np.arctan(np.sqrt(dx * dx + dy * dy))
    aspect = np.arctan2(-dx, dy)
    az = np.deg2rad(315.0)
    alt = np.deg2rad(45.0)
    hs = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    hs = (hs - np.nanmin(hs)) / max(np.nanmax(hs) - np.nanmin(hs), 1e-12)
    return np.where(valid, hs, np.nan)


def grid_xy(transform, width, height):
    x = transform.c + (np.arange(width) + 0.5) * transform.a
    y = transform.f + (np.arange(height) + 0.5) * transform.e
    return x, y


def extract_contours(arr, transform, nodata, interval=10.0):
    valid = np.isfinite(arr) & (arr != nodata)
    vals = arr[valid]
    mn, mx = float(vals.min()), float(vals.max())
    start = math.floor(mn / interval) * interval
    end = math.ceil(mx / interval) * interval
    levels = np.arange(start, end + interval, interval)

    x, y = grid_xy(transform, arr.shape[1], arr.shape[0])
    masked = np.ma.masked_where(~valid, arr)
    fig, ax = plt.subplots()
    cs = ax.contour(x, y, masked, levels=levels)
    features = []
    to_wgs84 = Transformer.from_crs(TARGET_CRS, "EPSG:4326", always_xy=True)

    for level, segs in zip(cs.levels, cs.allsegs):
        for seg in segs:
            if len(seg) < 2:
                continue
            line = LineString(seg)
            line4326 = shp_transform(to_wgs84.transform, line)
            features.append({
                "type": "Feature",
                "properties": {"elevation_m": float(level)},
                "geometry": mapping(line4326),
            })
    plt.close(fig)
    return {
        "type": "FeatureCollection",
        "name": f"Sabah Rail {int(interval)}m contours",
        "features": features,
    }, levels


def export_xyz(path: Path, arr, transform, nodata):
    valid = np.isfinite(arr) & (arr != nodata)
    rows, cols = np.where(valid)
    xs, ys = rasterio.transform.xy(transform, rows, cols, offset="center")
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=" ")
        for x, y, z in zip(xs, ys, arr[rows, cols]):
            w.writerow([f"{x:.3f}", f"{y:.3f}", f"{float(z):.3f}"])
    return int(len(rows))


def polygon_exteriors(geom):
    if geom.geom_type == "Polygon":
        yield geom.exterior
    elif geom.geom_type == "MultiPolygon":
        for g in geom.geoms:
            yield g.exterior


def line_geoms(geom):
    if geom.geom_type == "LineString":
        yield geom
    elif geom.geom_type == "MultiLineString":
        yield from geom.geoms


def make_map(out_png, dem_path, corridor, alignment, nodes, contour_fc, stats, show_alignment=False):
    with rasterio.open(dem_path) as ds:
        arr = ds.read(1)
        transform = ds.transform
        nodata = ds.nodata
        b = ds.bounds
        hs = hillshade(arr, transform, nodata)
        valid = np.isfinite(arr) & (arr != nodata)
        dem = np.where(valid, arr, np.nan)
        x, y = grid_xy(transform, ds.width, ds.height)

    to_utm = Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    corridor_u = shp_transform(to_utm.transform, corridor)
    alignment_u = shp_transform(to_utm.transform, alignment)
    nodes_u = [(name, shp_transform(to_utm.transform, g)) for name, g in nodes]

    fig, ax = plt.subplots(figsize=(11, 14))
    im = ax.imshow(
        dem,
        extent=[b.left, b.right, b.bottom, b.top],
        origin="upper",
        cmap="terrain",
        interpolation="bilinear",
    )
    ax.imshow(
        hs,
        extent=[b.left, b.right, b.bottom, b.top],
        origin="upper",
        cmap="gray",
        alpha=0.30,
        interpolation="bilinear",
    )

    levels = sorted({f["properties"]["elevation_m"] for f in contour_fc["features"]})
    if levels:
        ax.contour(
            x, y, np.ma.masked_invalid(dem), levels=levels,
            linewidths=0.35, alpha=0.55
        )

    for ext in polygon_exteriors(corridor_u):
        bx, by = ext.xy
        ax.plot(bx, by, linewidth=2.2, label="Study corridor")

    if show_alignment:
        first=True
        for ln in line_geoms(alignment_u):
            lx, ly = ln.xy
            ax.plot(lx, ly, linewidth=2.0, label="Feasible alignment (44.3 km)" if first else None)
            first=False

        for name, pt in nodes_u:
            ax.scatter([pt.x], [pt.y], s=28, zorder=5)
            ax.annotate(name, (pt.x, pt.y), xytext=(5, 5), textcoords="offset points", fontsize=8)

    # North arrow
    ax.annotate(
        "N", xy=(0.94, 0.94), xytext=(0.94, 0.84),
        xycoords="axes fraction", ha="center", va="center", fontsize=13,
        arrowprops=dict(arrowstyle="-|>", lw=1.4),
    )

    # 5 km scale bar
    spanx = b.right - b.left
    x0 = b.left + 0.08 * spanx
    y0 = b.bottom + 0.055 * (b.top - b.bottom)
    ax.plot([x0, x0 + 5000], [y0, y0], linewidth=3)
    ax.plot([x0, x0], [y0 - 120, y0 + 120], linewidth=1)
    ax.plot([x0 + 5000, x0 + 5000], [y0 - 120, y0 + 120], linewidth=1)
    ax.text(x0 + 2500, y0 + 250, "5 km", ha="center", fontsize=8)

    title = (
        "Sabah Rail Study Area — DEM & Topographic Map with Feasible Alignment"
        if show_alignment
        else "Sabah Rail Study Area — DEM & Topographic Base Map"
    )
    ax.set_title(
        title + "\nPutatan – KKIP – Sepanggar Port",
        fontsize=15, pad=14,
    )
    ax.set_xlabel("Easting (m) — WGS 84 / UTM Zone 50N")
    ax.set_ylabel("Northing (m)")
    ax.grid(True, linewidth=0.3, alpha=0.35)
    ax.legend(loc="upper left", fontsize=8)

    cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.025)
    cbar.set_label("Elevation (m)")

    ax.text(
        0.015, 0.015,
        f"Elevation: {stats['min_m']:.1f}–{stats['max_m']:.1f} m\n"
        f"Mean: {stats['mean_m']:.1f} m | Contours: 10 m",
        transform=ax.transAxes, fontsize=8,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.75),
    )

    map_note = (
        "Feasible alignment shown as a project overlay on the same terrain base."
        if show_alignment
        else "No railway alignment shown — terrain/topographic base only."
    )
    fig.text(
        0.01, 0.008,
        "Terrain: Copernicus DEM GLO-30 Public (2021, DSM). "
        "Study boundary: supplied Sabah Rail project GeoJSON. "
        + map_note
        + " Feasibility-level terrain reference; not a substitute for engineering survey.",
        fontsize=7.2,
    )
    fig.tight_layout(rect=[0, 0.025, 1, 1])
    fig.savefig(out_png, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/Sabah_Rail_Study_Area_and_Alignment.geojson")
    ap.add_argument("--output", default="output/sabah_rail_dem_topo")
    args = ap.parse_args()

    in_path = Path(args.input)
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    tile_dir = outdir / "_tiles"
    tile_dir.mkdir(exist_ok=True)

    fc, corridor, alignment, nodes = load_project_geojson(in_path)

    session = requests.Session()
    session.headers.update({"User-Agent": "TopographicStructureDEM-SabahRail/1.0"})

    tile_paths=[]
    tile_records=[]
    for lat, lon in needed_tiles(corridor.bounds):
        url = tile_url(lat, lon)
        dest = tile_dir / f"{tile_id(lat, lon)}.tif"
        download(url, dest, session)
        tile_paths.append(dest)
        tile_records.append({"tile": tile_id(lat, lon), "url": url})

    arr, trans, crs, nodata, profile = mosaic_and_clip(tile_paths, corridor)

    dem_wgs = outdir / "Sabah_Rail_DEM_GLO30_WGS84.tif"
    write_dem(dem_wgs, arr, profile)

    dem_utm = outdir / "Sabah_Rail_DEM_GLO30_UTM50N.tif"
    reproject_dem(dem_wgs, dem_utm)

    with rasterio.open(dem_utm) as ds:
        utm_arr = ds.read(1)
        utm_trans = ds.transform
        utm_nodata = ds.nodata

    valid = np.isfinite(utm_arr) & (utm_arr != utm_nodata)
    vals = utm_arr[valid].astype(float)
    stats = {
        "min_m": float(vals.min()),
        "max_m": float(vals.max()),
        "mean_m": float(vals.mean()),
        "median_m": float(np.median(vals)),
    }

    contours, levels = extract_contours(utm_arr, utm_trans, utm_nodata, interval=10.0)
    contour_path = outdir / "Sabah_Rail_Contours_10m.geojson"
    contour_path.write_text(json.dumps(contours), encoding="utf-8")

    xyz_path = outdir / "Sabah_Rail_DEM_UTM50N.xyz"
    xyz_points = export_xyz(xyz_path, utm_arr, utm_trans, utm_nodata)

    project_path = outdir / "Sabah_Rail_Study_Area_and_Alignment.geojson"
    project_path.write_text(json.dumps(fc, indent=2), encoding="utf-8")

    base_map_png = outdir / "Sabah_Rail_DEM_Topographic_Base_Map_NO_ALIGNMENT.png"
    overlay_map_png = outdir / "Sabah_Rail_DEM_Topographic_Alignment_Overlay.png"
    make_map(
        base_map_png, dem_utm, corridor, alignment, nodes, contours, stats,
        show_alignment=False
    )
    make_map(
        overlay_map_png, dem_utm, corridor, alignment, nodes, contours, stats,
        show_alignment=True
    )

    metadata = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "project": "Sabah Rail feasibility study — Putatan to KKIP and Sepanggar Port",
        "project_geometry": {
            "source_file": in_path.name,
            "corridor_feature": "Corridor Area",
            "alignment_feature": "Proposed Feasible Alignment_ 44.3km",
            "corridor_bounds_wgs84": [float(v) for v in corridor.bounds],
            "alignment_length_label": "44.3 km (project feature label)",
        },
        "dem_source": {
            "dataset": "Copernicus DEM GLO-30 Public",
            "release": "2021",
            "data_type": "DSM",
            "aws_bucket": "s3://copernicus-dem-30m/",
            "tiles": tile_records,
        },
        "processing": {
            "clip": "Exact project Corridor Area polygon",
            "wgs84_crs": "EPSG:4326",
            "engineering_crs": TARGET_CRS,
            "contour_interval_m": 10,
            "xyz_crs": TARGET_CRS,
            "xyz_point_count": xyz_points,
        },
        "elevation_statistics_m": stats,
        "limitations": [
            "Copernicus GLO-30 is a DSM, not a bare-earth engineering survey surface.",
            "Suitable for feasibility and regional terrain screening; verify with survey/LiDAR for final design.",
            "Contour interval does not imply survey-grade vertical accuracy.",
        ],
        "map_versions": {
            "clean_base_map": base_map_png.name,
            "alignment_overlay_map": overlay_map_png.name,
            "clean_base_definition": (
                "DEM/topography, 10 m contours, elevation scale, north arrow, "
                "scale bar and study-area boundary only; no railway alignment or project nodes."
            ),
            "overlay_definition": (
                "Same terrain base with Proposed Feasible Alignment_ 44.3km "
                "and project node labels overlaid."
            ),
        },
        "outputs": [
            base_map_png.name,
            overlay_map_png.name,
            dem_wgs.name,
            dem_utm.name,
            contour_path.name,
            xyz_path.name,
            project_path.name,
        ],
    }
    (outdir / "Sabah_Rail_DEM_Topo_Metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
