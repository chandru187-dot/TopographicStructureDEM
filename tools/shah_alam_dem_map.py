#!/usr/bin/env python3
"""
Build a real Shah Alam topographic/DEM map.

Inputs
------
1. Official MBSA Shah Alam boundary (ArcGIS REST Feature Layer)
2. AWS Open Data Terrain Tiles (Terrarium encoding)

Outputs
-------
- shah_alam_topographic_map.png
- shah_alam_dem_epsg3857.tif
- shah_alam_dem_wgs84.tif
- shah_alam_boundary.geojson
- shah_alam_metadata.json

This script is an engineering-oriented extension of the repository's original
DEM/topographic-structure concept. The original CE710 script uses a hard-coded
10x10 sample DEM; this workflow operates on a real georeferenced terrain raster.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, Resampling

from pyproj import Transformer
from shapely.geometry import shape, mapping, Polygon, MultiPolygon
from shapely.ops import unary_union, transform as shapely_transform


MBSA_LAYER = (
    "https://imapsa.mbsa.gov.my/server/rest/services/"
    "TABURAN_SISTEM_SALIRAN_MBSA/MapServer/0"
)
MBSA_QUERY = MBSA_LAYER + "/query"
JPS_PBT_LAYER = (
    "https://jpsselgis.selangor.gov.my/gis/rest/services/"
    "_PBT/MapServer/0"
)
JPS_PBT_QUERY = JPS_PBT_LAYER + "/query"
TERRARIUM_TEMPLATE = (
    "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
)

# Official MBSA layer extent published in EPSG:3380.
# Used only as a fallback if the live geometry query is unavailable.
MBSA_EXTENT_EPSG3380 = {
    "xmin": -30630.147800000384,
    "ymin": -24833.150399999693,
    "xmax": -12367.92100000009,
    "ymax": 6173.444499999285,
}


def fetch_mbsa_boundary(session: requests.Session):
    params = {
        "where": "1=1",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    try:
        r = session.get(MBSA_QUERY, params=params, timeout=45)
        r.raise_for_status()
        data = r.json()
        feats = data.get("features", [])
        if not feats:
            raise RuntimeError("MBSA query returned no features")
        geom = unary_union([shape(f["geometry"]) for f in feats])
        if geom.is_empty:
            raise RuntimeError("MBSA boundary geometry is empty")
        return geom, data, True, None
    except Exception as exc:
        primary_error = repr(exc)

        # Secondary official source: Selangor JPS PBT boundary layer.
        # Fetch the small PBT layer and select the Shah Alam local-authority polygon.
        try:
            params2 = {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
            }
            r2 = session.get(JPS_PBT_QUERY, params=params2, timeout=45)
            r2.raise_for_status()
            data2 = r2.json()
            feats2 = data2.get("features", [])
            matches = []
            for f in feats2:
                props_text = json.dumps(f.get("properties", {}), ensure_ascii=False).upper()
                if "SHAH ALAM" in props_text or "MBSA" in props_text:
                    matches.append(f)
            if not matches:
                raise RuntimeError("JPS PBT query returned no Shah Alam feature")
            geom = unary_union([shape(f["geometry"]) for f in matches])
            if geom.is_empty:
                raise RuntimeError("JPS Shah Alam boundary geometry is empty")
            data = {"type": "FeatureCollection", "features": matches}
            note = (
                "Primary MBSA geometry query failed; used official Selangor JPS "
                "PBT polygon instead. Primary error: " + primary_error
            )
            return geom, data, True, note
        except Exception as exc2:
            secondary_error = repr(exc2)

        # Final fallback uses the official MBSA layer extent, transformed to WGS84.
        tr = Transformer.from_crs(3380, 4326, always_xy=True)
        minlon, minlat = tr.transform(
            MBSA_EXTENT_EPSG3380["xmin"], MBSA_EXTENT_EPSG3380["ymin"]
        )
        maxlon, maxlat = tr.transform(
            MBSA_EXTENT_EPSG3380["xmax"], MBSA_EXTENT_EPSG3380["ymax"]
        )
        geom = Polygon([
            (minlon, minlat),
            (maxlon, minlat),
            (maxlon, maxlat),
            (minlon, maxlat),
            (minlon, minlat),
        ])
        data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "source": "MBSA published layer extent fallback",
                        "epsg3380_extent": MBSA_EXTENT_EPSG3380,
                    },
                    "geometry": mapping(geom),
                }
            ],
        }
        note = (
            "Both live boundary queries failed. MBSA: " + primary_error
            + " | JPS PBT: " + secondary_error
        )
        return geom, data, False, note


def lonlat_to_tile(lon: float, lat: float, z: int):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int(
        (1.0 - math.asinh(math.tan(lat_rad)) / math.pi)
        / 2.0
        * n
    )
    return x, y


def mercator_tile_bounds(x: int, y: int, z: int):
    origin = 20037508.342789244
    n = 2 ** z
    span = 2 * origin / n
    left = -origin + x * span
    right = left + span
    top = origin - y * span
    bottom = top - span
    return left, bottom, right, top


def download_terrarium_tile(session: requests.Session, z: int, x: int, y: int):
    url = TERRARIUM_TEMPLATE.format(z=z, x=x, y=y)
    r = session.get(url, timeout=60)
    r.raise_for_status()
    im = Image.open(BytesIO(r.content)).convert("RGB")
    arr = np.asarray(im, dtype=np.float32)
    # Terrarium decoding:
    # elevation = (R * 256 + G + B / 256) - 32768
    dem = (arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0) - 32768.0
    return dem


def build_mosaic(session: requests.Session, bbox, zoom: int):
    minlon, minlat, maxlon, maxlat = bbox

    x_min, y_max = lonlat_to_tile(minlon, minlat, zoom)
    x_max, y_min = lonlat_to_tile(maxlon, maxlat, zoom)

    xs = list(range(x_min, x_max + 1))
    ys = list(range(y_min, y_max + 1))

    mosaic = np.empty((len(ys) * 256, len(xs) * 256), dtype=np.float32)

    for ix, x in enumerate(xs):
        for iy, y in enumerate(ys):
            tile = download_terrarium_tile(session, zoom, x, y)
            r0 = iy * 256
            c0 = ix * 256
            mosaic[r0:r0 + 256, c0:c0 + 256] = tile

    left, _, _, top = mercator_tile_bounds(xs[0], ys[0], zoom)
    _, bottom, right, _ = mercator_tile_bounds(xs[-1], ys[-1], zoom)
    pixel = (right - left) / mosaic.shape[1]
    transform = from_origin(left, top, pixel, pixel)

    return mosaic, transform, (left, bottom, right, top), {
        "zoom": zoom,
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
        "tile_count": len(xs) * len(ys),
        "pixel_size_m_approx": pixel,
    }


def hillshade(elevation, pixel_size_m, azimuth=315.0, altitude=45.0):
    # Fill NaNs for gradient calculation.
    arr = np.asarray(elevation, dtype=np.float64)
    if np.isnan(arr).any():
        fill = np.nanmedian(arr)
        arr = np.where(np.isnan(arr), fill, arr)

    dy, dx = np.gradient(arr, pixel_size_m, pixel_size_m)
    slope = np.pi / 2.0 - np.arctan(np.sqrt(dx * dx + dy * dy))
    aspect = np.arctan2(-dx, dy)

    az = np.deg2rad(azimuth)
    alt = np.deg2rad(altitude)
    shaded = (
        np.sin(alt) * np.sin(slope)
        + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    )
    shaded = (shaded - shaded.min()) / max(shaded.max() - shaded.min(), 1e-9)
    return shaded


def iter_polygon_exteriors(geom):
    if isinstance(geom, Polygon):
        yield geom.exterior
    elif isinstance(geom, MultiPolygon):
        for g in geom.geoms:
            yield g.exterior
    else:
        for g in getattr(geom, "geoms", []):
            yield from iter_polygon_exteriors(g)


def write_geotiff(path, data, transform, crs, nodata=-9999.0):
    out = np.where(np.isfinite(data), data, nodata).astype(np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=out.shape[0],
        width=out.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="deflate",
        predictor=3,
        tiled=True,
    ) as dst:
        dst.write(out, 1)


def reproject_to_wgs84(src_path: Path, dst_path: Path):
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, "EPSG:4326", src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update({
            "crs": "EPSG:4326",
            "transform": transform,
            "width": width,
            "height": height,
            "compress": "deflate",
            "predictor": 3,
            "tiled": True,
        })
        with rasterio.open(dst_path, "w", **kwargs) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=transform,
                dst_crs="EPSG:4326",
                dst_nodata=src.nodata,
                resampling=Resampling.bilinear,
            )


def make_map(out_png: Path, dem, mask, transform, bounds, geom3857):
    left, bottom, right, top = bounds
    pixel = transform.a
    clipped = np.where(mask, dem, np.nan)
    hs = hillshade(dem, pixel)
    hs = np.where(mask, hs, np.nan)

    valid = clipped[np.isfinite(clipped)]
    elev_min = float(np.nanmin(valid))
    elev_max = float(np.nanmax(valid))

    fig, ax = plt.subplots(figsize=(10, 14))
    im = ax.imshow(
        clipped,
        extent=[left, right, bottom, top],
        origin="upper",
        cmap="terrain",
        interpolation="bilinear",
    )
    ax.imshow(
        hs,
        extent=[left, right, bottom, top],
        origin="upper",
        cmap="gray",
        alpha=0.28,
        interpolation="bilinear",
    )

    # Contours
    x = left + (np.arange(dem.shape[1]) + 0.5) * pixel
    y = top - (np.arange(dem.shape[0]) + 0.5) * pixel
    interval = 10.0 if (elev_max - elev_min) <= 300 else 25.0
    start = math.floor(elev_min / interval) * interval
    stop = math.ceil(elev_max / interval) * interval
    levels = np.arange(start, stop + interval, interval)
    if len(levels) >= 2:
        ax.contour(
            x, y, np.ma.masked_invalid(clipped),
            levels=levels, linewidths=0.35, alpha=0.55
        )

    # Boundary
    for ext in iter_polygon_exteriors(geom3857):
        bx, by = ext.xy
        ax.plot(bx, by, linewidth=2.0)

    # WGS84 tick labels while plotting in Web Mercator.
    inv = Transformer.from_crs(3857, 4326, always_xy=True)
    xmid = (left + right) / 2
    ymid = (bottom + top) / 2

    ax.xaxis.set_major_formatter(
        FuncFormatter(lambda xv, pos: f"{inv.transform(xv, ymid)[0]:.3f}°E")
    )
    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda yv, pos: f"{inv.transform(xmid, yv)[1]:.3f}°N")
    )

    ax.set_title(
        "Shah Alam Topographic / DEM Map\n"
        "Official MBSA boundary + AWS Open Data Terrain Tiles",
        fontsize=15,
        pad=16,
    )
    ax.set_xlabel("Longitude (WGS84)")
    ax.set_ylabel("Latitude (WGS84)")
    ax.grid(True, linewidth=0.3, alpha=0.35)

    cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.025)
    cbar.set_label("Elevation (m)")

    # North arrow
    ax.annotate(
        "N",
        xy=(0.94, 0.94),
        xytext=(0.94, 0.84),
        xycoords="axes fraction",
        ha="center",
        va="center",
        fontsize=13,
        arrowprops=dict(arrowstyle="-|>", lw=1.4),
    )

    fig.text(
        0.01, 0.01,
        "Terrain: AWS Open Data Terrain Tiles (Mapzen/Joerd). "
        "Boundary: Majlis Bandaraya Shah Alam (MBSA).",
        fontsize=7.5,
    )
    fig.tight_layout(rect=[0, 0.025, 1, 1])
    fig.savefig(out_png, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="output/shah_alam")
    parser.add_argument("--zoom", type=int, default=13)
    parser.add_argument(
        "--margin-deg", type=float, default=0.008,
        help="Small margin around MBSA boundary when fetching terrain."
    )
    args = parser.parse_args()

    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "TopographicStructureDEM-ShahAlam/1.0 (+GitHub Actions)"
    })

    geom4326, boundary_geojson, boundary_live, boundary_error = fetch_mbsa_boundary(session)

    boundary_path = outdir / "shah_alam_boundary.geojson"
    boundary_path.write_text(json.dumps(boundary_geojson, indent=2), encoding="utf-8")

    minlon, minlat, maxlon, maxlat = geom4326.bounds
    bbox = (
        minlon - args.margin_deg,
        minlat - args.margin_deg,
        maxlon + args.margin_deg,
        maxlat + args.margin_deg,
    )

    dem, transform, bounds3857, tiles_meta = build_mosaic(session, bbox, args.zoom)

    to3857 = Transformer.from_crs(4326, 3857, always_xy=True)
    geom3857 = shapely_transform(to3857.transform, geom4326)

    inside = geometry_mask(
        [mapping(geom3857)],
        out_shape=dem.shape,
        transform=transform,
        invert=True,
        all_touched=False,
    )
    clipped = np.where(inside, dem, np.nan)

    tif3857 = outdir / "shah_alam_dem_epsg3857.tif"
    tif4326 = outdir / "shah_alam_dem_wgs84.tif"
    write_geotiff(tif3857, clipped, transform, "EPSG:3857")
    reproject_to_wgs84(tif3857, tif4326)

    png = outdir / "shah_alam_topographic_map.png"
    make_map(png, dem, inside, transform, bounds3857, geom3857)

    valid = clipped[np.isfinite(clipped)]
    metadata = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "study_area": "Shah Alam, Selangor, Malaysia",
        "boundary_source": {
            "name": "Official Shah Alam boundary services",
            "primary_mbsa_layer_url": MBSA_LAYER,
            "secondary_selangor_jps_pbt_layer_url": JPS_PBT_LAYER,
            "query_used_live_geometry": boundary_live,
            "source_note_or_error": boundary_error,
            "wgs84_bounds": [float(v) for v in geom4326.bounds],
        },
        "terrain_source": {
            "name": "AWS Open Data Terrain Tiles / Mapzen Joerd",
            "terrarium_template": TERRARIUM_TEMPLATE,
            "encoding_formula": "(R*256 + G + B/256) - 32768",
            **tiles_meta,
        },
        "elevation_statistics_m": {
            "min": float(np.nanmin(valid)),
            "max": float(np.nanmax(valid)),
            "mean": float(np.nanmean(valid)),
            "median": float(np.nanmedian(valid)),
        },
        "outputs": {
            "map_png": png.name,
            "dem_epsg3857": tif3857.name,
            "dem_wgs84": tif4326.name,
            "boundary_geojson": boundary_path.name,
        },
    }
    (outdir / "shah_alam_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
