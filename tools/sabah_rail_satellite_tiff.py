#!/usr/bin/env python3
"""
Build a georeferenced Sentinel-2 RGB satellite GeoTIFF for the Sabah Rail study area.

- Uses the verified project corridor GeoJSON.
- Searches Element 84 Earth Search STAC for Sentinel-2 Level-2A COGs.
- Builds a recent cloud-reduced RGB mosaic at 10 m resolution.
- Writes UTM Zone 50N and WGS84 GeoTIFFs.
- Also writes an exact-corridor clipped GeoTIFF and a PNG preview.

This is optical satellite imagery, not a cadastral or survey product.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse

import numpy as np
import requests
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.mask import mask
from rasterio.warp import calculate_default_transform, reproject
from rasterio.features import geometry_mask
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pyproj import Transformer
from shapely.geometry import shape, mapping
from shapely.ops import transform as shp_transform

STAC_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
TARGET_CRS = "EPSG:32650"
RES_M = 10.0

# Sentinel-2 Scene Classification Layer classes to reject:
# 0 nodata, 1 saturated/defective, 3 cloud shadow,
# 8 medium cloud, 9 high cloud, 10 cirrus, 11 snow/ice.
BAD_SCL = np.array([0, 1, 3, 8, 9, 10, 11], dtype=np.uint8)


def load_corridor(path: Path):
    fc = json.loads(path.read_text(encoding="utf-8"))
    corridor = None
    for f in fc.get("features", []):
        name = str(f.get("properties", {}).get("name", ""))
        g = shape(f["geometry"])
        if g.geom_type in ("Polygon", "MultiPolygon") and "corridor" in name.lower():
            corridor = g
            break
    if corridor is None:
        raise RuntimeError("Corridor Area polygon not found in project GeoJSON")
    return corridor


def snap_bounds(bounds, res=RES_M):
    minx, miny, maxx, maxy = bounds
    minx = math.floor(minx / res) * res
    miny = math.floor(miny / res) * res
    maxx = math.ceil(maxx / res) * res
    maxy = math.ceil(maxy / res) * res
    return minx, miny, maxx, maxy


def search_sentinel(intersects, months=18, cloud_lt=35, limit=60):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(months * 30.44))
    payload = {
        "collections": [COLLECTION],
        "intersects": mapping(intersects),
        "datetime": f"{start.date().isoformat()}/{end.date().isoformat()}",
        "limit": limit,
    }
    r = requests.post(STAC_URL, json=payload, timeout=120)
    if not r.ok:
        raise RuntimeError(f"Earth Search STAC error {r.status_code}: {r.text[:1000]}")
    js = r.json()
    feats = js.get("features", [])
    if not feats:
        raise RuntimeError("No Sentinel-2 scenes found for the Sabah Rail study area.")

    # Apply the requested scene-cloud threshold locally so the workflow
    # remains compatible with STAC servers that do not enable the Query extension.
    filtered = []
    for item in feats:
        cc = item.get("properties", {}).get("eo:cloud_cover")
        if cc is None or float(cc) < cloud_lt:
            filtered.append(item)
    if filtered:
        feats = filtered

    def dt(item):
        return item.get("properties", {}).get("datetime", "")
    def cloud(item):
        v = item.get("properties", {}).get("eo:cloud_cover")
        return 999.0 if v is None else float(v)

    # Keep the best recent candidates. Cloud cover is prioritized,
    # then acquisition date. A small recentness penalty avoids very old scenes.
    now = end
    def score(item):
        d = datetime.fromisoformat(dt(item).replace("Z", "+00:00"))
        age_days = max((now - d).days, 0)
        return cloud(item) + min(age_days / 180.0, 5.0)

    feats.sort(key=score)
    # Use a manageable set for a cloud-reduced mosaic.
    return feats[:8], start, end


def item_assets(item):
    assets = item.get("assets", {})
    visual = assets.get("visual")
    scl = assets.get("scl")
    if not visual or not scl:
        raise RuntimeError(
            f"Required visual/scl assets missing for STAC item {item.get('id')}"
        )
    return visual["href"], scl["href"]


def build_composite(items, transform, width, height):
    rgb = np.zeros((3, height, width), dtype=np.uint8)
    filled = np.zeros((height, width), dtype=bool)
    used = []

    for item in items:
        visual_href, scl_href = item_assets(item)
        scene_id = item.get("id")
        props = item.get("properties", {})
        print("Reading", scene_id, props.get("datetime"), props.get("eo:cloud_cover"))

        with rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF",
            GDAL_HTTP_MULTIRANGE="YES",
            VSI_CACHE="TRUE",
            VSI_CACHE_SIZE="50000000",
        ):
            with rasterio.open(visual_href) as src:
                with WarpedVRT(
                    src,
                    crs=TARGET_CRS,
                    transform=transform,
                    width=width,
                    height=height,
                    resampling=Resampling.bilinear,
                    nodata=0,
                ) as vrt:
                    scene_rgb = vrt.read([1, 2, 3], out_dtype="uint8")

            with rasterio.open(scl_href) as src:
                with WarpedVRT(
                    src,
                    crs=TARGET_CRS,
                    transform=transform,
                    width=width,
                    height=height,
                    resampling=Resampling.nearest,
                    nodata=0,
                ) as vrt:
                    scl = vrt.read(1, out_dtype="uint8")

        valid = (~np.isin(scl, BAD_SCL)) & np.any(scene_rgb > 0, axis=0)
        take = valid & (~filled)
        n = int(take.sum())
        if n:
            rgb[:, take] = scene_rgb[:, take]
            filled[take] = True
            used.append({
                "id": scene_id,
                "datetime": props.get("datetime"),
                "eo_cloud_cover_pct": props.get("eo:cloud_cover"),
                "new_pixels_filled": n,
                "visual_href": visual_href,
                "scl_href": scl_href,
            })
        if filled.mean() >= 0.995:
            break

    return rgb, filled, used


def write_rgb_tif(path, rgb, transform, crs=TARGET_CRS):
    profile = {
        "driver": "GTiff",
        "height": rgb.shape[1],
        "width": rgb.shape[2],
        "count": 3,
        "dtype": "uint8",
        "crs": crs,
        "transform": transform,
        "photometric": "RGB",
        "compress": "DEFLATE",
        "predictor": 2,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "nodata": 0,
        "BIGTIFF": "IF_SAFER",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(rgb)
        dst.set_band_description(1, "Red")
        dst.set_band_description(2, "Green")
        dst.set_band_description(3, "Blue")
        factors = [2, 4, 8, 16, 32]
        dst.build_overviews(factors, Resampling.average)
        dst.update_tags(ns="rio_overview", resampling="average")


def reproject_rgb(src_path, dst_path, dst_crs="EPSG:4326"):
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
            compress="DEFLATE",
            tiled=True,
            blockxsize=512,
            blockysize=512,
            BIGTIFF="IF_SAFER",
        )
        with rasterio.open(dst_path, "w", **profile) as dst:
            for b in range(1, 4):
                reproject(
                    source=rasterio.band(src, b),
                    destination=rasterio.band(dst, b),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=0,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    dst_nodata=0,
                    resampling=Resampling.bilinear,
                )
            dst.build_overviews([2, 4, 8, 16, 32], Resampling.average)


def clip_to_corridor(src_path, corridor_wgs84, dst_path):
    with rasterio.open(src_path) as src:
        tx = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        geom = shp_transform(tx.transform, corridor_wgs84)
        data, trans = mask(
            src, [mapping(geom)], crop=True, nodata=0, filled=True
        )
        profile = src.profile.copy()
        profile.update(
            height=data.shape[1],
            width=data.shape[2],
            transform=trans,
            compress="DEFLATE",
            tiled=True,
            BIGTIFF="IF_SAFER",
        )
        with rasterio.open(dst_path, "w", **profile) as dst:
            dst.write(data)


def make_preview(path, rgb, transform, corridor_utm, used):
    img = np.moveaxis(rgb, 0, -1)
    h, w = rgb.shape[1:]
    left = transform.c
    top = transform.f
    right = left + w * transform.a
    bottom = top + h * transform.e

    fig, ax = plt.subplots(figsize=(13.333, 7.5))
    ax.imshow(img, extent=[left, right, bottom, top], origin="upper")
    if corridor_utm.geom_type == "Polygon":
        geoms = [corridor_utm]
    else:
        geoms = list(corridor_utm.geoms)
    for g in geoms:
        x, y = g.exterior.xy
        ax.plot(x, y, linewidth=1.7, color="white")
        ax.plot(x, y, linewidth=0.8, color="black")

    ax.set_title("Sabah Rail Study Area — Sentinel-2 Satellite Imagery", fontsize=15)
    ax.set_xlabel("Easting (m) — WGS 84 / UTM Zone 50N")
    ax.set_ylabel("Northing (m)")
    if used:
        dates = [u["datetime"][:10] for u in used if u.get("datetime")]
        note = f"Cloud-reduced mosaic from {len(used)} Sentinel-2 L2A scene(s)"
        if dates:
            note += f" | {min(dates)} to {max(dates)}"
        ax.text(
            0.01, 0.015, note,
            transform=ax.transAxes, fontsize=8,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white", alpha=0.78),
        )
    fig.tight_layout()
    fig.savefig(path, dpi=240, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input",
        default="data/Sabah_Rail_Study_Area_and_Alignment.geojson",
    )
    ap.add_argument(
        "--output",
        default="output/sabah_rail_satellite",
    )
    ap.add_argument("--buffer-m", type=float, default=3000.0)
    args = ap.parse_args()

    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)

    corridor = load_corridor(Path(args.input))
    tx = Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    corridor_utm = shp_transform(tx.transform, corridor)
    context_utm = corridor_utm.buffer(args.buffer_m)
    minx, miny, maxx, maxy = snap_bounds(context_utm.bounds, RES_M)

    width = int(round((maxx - minx) / RES_M))
    height = int(round((maxy - miny) / RES_M))
    transform = from_origin(minx, maxy, RES_M, RES_M)

    items, search_start, search_end = search_sentinel(corridor, months=18, cloud_lt=35)
    print("Candidate scenes:", len(items))
    for i in items:
        print(i.get("id"), i.get("properties", {}).get("datetime"), i.get("properties", {}).get("eo:cloud_cover"))

    rgb, filled, used = build_composite(items, transform, width, height)

    # Limit to context polygon rather than the rectangular working grid.
    context_mask = geometry_mask(
        [mapping(context_utm)],
        out_shape=(height, width),
        transform=transform,
        invert=True,
    )
    rgb[:, ~context_mask] = 0

    coverage = float((filled & context_mask).sum() / max(context_mask.sum(), 1))

    utm_path = outdir / "Sabah_Rail_Sentinel2_RGB_10m_UTM50N.tif"
    write_rgb_tif(utm_path, rgb, transform, TARGET_CRS)

    corridor_path = outdir / "Sabah_Rail_Sentinel2_RGB_10m_Corridor_UTM50N.tif"
    clip_to_corridor(utm_path, corridor, corridor_path)

    wgs_path = outdir / "Sabah_Rail_Sentinel2_RGB_10m_WGS84.tif"
    reproject_rgb(utm_path, wgs_path, "EPSG:4326")

    preview = outdir / "Sabah_Rail_Sentinel2_RGB_Preview.png"
    make_preview(preview, rgb, transform, corridor_utm, used)

    metadata = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "project": "Sabah Rail study area",
        "imagery": {
            "dataset": "Sentinel-2 Level-2A",
            "catalog": "Element 84 Earth Search v1",
            "collection": COLLECTION,
            "search_window": [
                search_start.date().isoformat(),
                search_end.date().isoformat(),
            ],
            "resolution_m": RES_M,
            "composite_method": (
                "Recent cloud-reduced RGB mosaic. Candidate scenes are ranked by "
                "scene cloud cover with a small recency penalty; pixels flagged as "
                "cloud, cirrus, cloud shadow, snow/ice, saturated, or nodata in SCL "
                "are rejected and filled from the next candidate scene."
            ),
            "used_scenes": used,
            "valid_context_coverage_pct": round(coverage * 100, 3),
        },
        "spatial": {
            "primary_crs": TARGET_CRS,
            "wgs84_crs": "EPSG:4326",
            "context_buffer_m": args.buffer_m,
            "pixel_size_m": RES_M,
            "width_px": width,
            "height_px": height,
            "context_bounds_utm50n": [minx, miny, maxx, maxy],
            "corridor_bounds_wgs84": list(map(float, corridor.bounds)),
        },
        "limitations": [
            "Optical imagery may contain residual haze/cloud or temporal seams.",
            "Composite pixels can originate from different acquisition dates.",
            "Sentinel-2 imagery is not a cadastral or engineering survey product.",
        ],
        "outputs": [
            utm_path.name,
            corridor_path.name,
            wgs_path.name,
            preview.name,
        ],
    }
    (outdir / "Sabah_Rail_Sentinel2_Metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
