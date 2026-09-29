#!/usr/bin/env python3
"""Build the authorized Esri high-resolution imagery package for Sabah Rail.

The workflow deliberately separates two Esri-supported uses:

* Static presentation imagery is rendered through the public World Imagery
  MapServer export operation and includes the required attribution.
* Authenticated offline imagery is requested from the official
  "World Imagery (for Export)" service as a TPK/TPKX tile package.

The export service is not a direct GeoTIFF download service.  This program does
not unpack or convert the licensed tile package into a different raster format.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from PIL import Image, ImageDraw, ImageFont, ImageStat
from pyproj import Transformer
from shapely.geometry import Point, box, shape
from shapely.geometry.polygon import orient
from shapely.ops import transform as shp_transform


PUBLIC_SERVICE = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer"
)
EXPORT_SERVICE = (
    "https://tiledbasemaps.arcgis.com/arcgis/rest/services/"
    "World_Imagery/MapServer"
)
EXPORT_ITEM_ID = "226d23f076da478bba4589e7eae95952"
EXPORT_ITEM_URL = f"https://www.arcgis.com/home/item.html?id={EXPORT_ITEM_ID}"
TARGET_CRS = "EPSG:32650"
WEB_MERCATOR = "EPSG:3857"
WGS84 = "EPSG:4326"
ATTRIBUTION = (
    "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community"
)
LOD_RESOLUTIONS = [156543.03392800014 / (2**level) for level in range(24)]
USER_AGENT = "SabahRailAuthorizedImageryWorkflow/1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    adapter = requests.adapters.HTTPAdapter(max_retries=3)
    session.mount("https://", adapter)
    return session


def load_project_geometry(path: Path):
    feature_collection = json.loads(path.read_text(encoding="utf-8"))
    corridor = None
    alignment = None
    for feature in feature_collection.get("features", []):
        geom = shape(feature["geometry"])
        name = str(feature.get("properties", {}).get("name", ""))
        if geom.geom_type in ("Polygon", "MultiPolygon") and "corridor" in name.lower():
            corridor = geom
        if geom.geom_type in ("LineString", "MultiLineString") and "alignment" in name.lower():
            alignment = geom
    if corridor is None:
        raise RuntimeError("Corridor Area polygon was not found in the project GeoJSON")
    if alignment is None:
        raise RuntimeError("Proposed alignment line was not found in the project GeoJSON")
    return feature_collection, corridor, alignment


def transform_geometry(geom, source: str, destination: str):
    transformer = Transformer.from_crs(source, destination, always_xy=True)
    return shp_transform(transformer.transform, geom)


def build_context(corridor_wgs84, buffer_m: float):
    corridor_utm = transform_geometry(corridor_wgs84, WGS84, TARGET_CRS)
    context_utm = corridor_utm.buffer(buffer_m)
    context_wgs84 = transform_geometry(context_utm, TARGET_CRS, WGS84)
    context_mercator = transform_geometry(context_utm, TARGET_CRS, WEB_MERCATOR)
    return corridor_utm, context_utm, context_wgs84, context_mercator


def arcgis_polygon(geom, wkid: int) -> dict[str, Any]:
    """Convert a Shapely polygon to ArcGIS REST polygon JSON."""
    polygons = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    rings = []
    for polygon in polygons:
        # ArcGIS REST uses clockwise exterior rings and counter-clockwise holes.
        polygon = orient(polygon, sign=-1.0)
        rings.append([list(map(float, xy)) for xy in polygon.exterior.coords])
        for interior in polygon.interiors:
            rings.append([list(map(float, xy)) for xy in interior.coords])
    return {"rings": rings, "spatialReference": {"wkid": wkid}}


def interpolate_points(line, count: int) -> list[Point]:
    if count < 2:
        return [line.interpolate(0.5, normalized=True)]
    return [line.interpolate(i / (count - 1), normalized=True) for i in range(count)]


def make_audit_points(alignment_wgs84, context_utm, spacing_m: float) -> list[dict[str, Any]]:
    alignment_utm = transform_geometry(alignment_wgs84, WGS84, TARGET_CRS)
    samples: list[tuple[str, Point]] = []
    for index, point in enumerate(interpolate_points(alignment_utm, 41)):
        samples.append((f"alignment_{index:02d}", point))

    minx, miny, maxx, maxy = context_utm.bounds
    x = math.floor(minx / spacing_m) * spacing_m
    grid_index = 0
    while x <= maxx:
        y = math.floor(miny / spacing_m) * spacing_m
        while y <= maxy:
            point = Point(x, y)
            if context_utm.covers(point):
                samples.append((f"context_{grid_index:03d}", point))
                grid_index += 1
            y += spacing_m
        x += spacing_m

    to_wgs84 = Transformer.from_crs(TARGET_CRS, WGS84, always_xy=True)
    seen: set[tuple[float, float]] = set()
    output = []
    for sample_id, point in samples:
        lon, lat = to_wgs84.transform(point.x, point.y)
        key = (round(lon, 6), round(lat, 6))
        if key in seen:
            continue
        seen.add(key)
        output.append({"sample_id": sample_id, "longitude": lon, "latitude": lat})
    return output


def query_current_source(sample: dict[str, Any]) -> dict[str, Any]:
    lon = sample["longitude"]
    lat = sample["latitude"]
    params = {
        "f": "json",
        "where": "1=1",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outFields": (
            "SRC_DATE,SRC_RES,SRC_ACC,SRC_DESC,NICE_NAME,NICE_DESC,"
            "SRC_DATE2,MinMapLevel,MaxMapLevel,DrawOrder,BlockName,ReleaseName"
        ),
        "returnGeometry": "false",
    }
    response = requests.get(
        f"{PUBLIC_SERVICE}/0/query",
        params=params,
        timeout=60,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    features = payload.get("features", [])
    record = dict(sample)
    if not features:
        record.update({"covered": False})
        return record
    attrs = features[0].get("attributes", {})
    record.update(
        {
            "covered": True,
            "acquisition_date": attrs.get("SRC_DATE"),
            "native_resolution_m": attrs.get("SRC_RES"),
            "horizontal_accuracy_m": attrs.get("SRC_ACC"),
            "sensor_or_product": attrs.get("SRC_DESC"),
            "product": attrs.get("NICE_NAME"),
            "provider": attrs.get("NICE_DESC"),
            "minimum_lod": attrs.get("MinMapLevel"),
            "maximum_lod": attrs.get("MaxMapLevel"),
            "block_name": attrs.get("BlockName"),
            "release_name": attrs.get("ReleaseName"),
        }
    )
    return record


def query_intersecting_sources(context_wgs84) -> list[dict[str, Any]]:
    params = {
        "f": "json",
        "where": "1=1",
        "geometry": json.dumps(arcgis_polygon(context_wgs84, 4326), separators=(",", ":")),
        "geometryType": "esriGeometryPolygon",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outFields": (
            "SRC_DATE,SRC_RES,SRC_ACC,SRC_DESC,NICE_NAME,NICE_DESC,"
            "MinMapLevel,MaxMapLevel,BlockName,ReleaseName"
        ),
        "returnGeometry": "false",
        "resultRecordCount": "100",
    }
    session = http_session()
    response = session.post(f"{PUBLIC_SERVICE}/0/query", data=params, timeout=120)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return [feature.get("attributes", {}) for feature in payload.get("features", [])]


def date_string(value: Any) -> str | None:
    if value in (None, "", "Null"):
        return None
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def run_coverage_audit(
    alignment_wgs84,
    context_utm,
    context_wgs84,
    outdir: Path,
    spacing_m: float,
) -> dict[str, Any]:
    points = make_audit_points(alignment_wgs84, context_utm, spacing_m)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(query_current_source, point): point for point in points}
        for future in as_completed(futures):
            point = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # retain audit location and fail clearly in summary
                failures.append({"sample_id": point["sample_id"], "error": str(exc)})
    results.sort(key=lambda row: row["sample_id"])

    footprints = query_intersecting_sources(context_wgs84)
    covered = [row for row in results if row.get("covered")]
    uncovered = [row for row in results if not row.get("covered")]
    sources = sorted({str(row.get("provider")) for row in covered if row.get("provider")})
    products = sorted({str(row.get("product")) for row in covered if row.get("product")})
    dates = sorted({date_string(row.get("acquisition_date")) for row in covered} - {None})
    resolutions = sorted(
        {float(row["native_resolution_m"]) for row in covered if row.get("native_resolution_m") is not None}
    )
    max_lods = sorted(
        {int(row["maximum_lod"]) for row in covered if row.get("maximum_lod") is not None}
    )

    audit = {
        "checked_utc": utc_now(),
        "service": PUBLIC_SERVICE,
        "method": (
            "41 evenly spaced alignment samples plus a regular grid throughout the "
            f"buffered study context at {spacing_m:g} m spacing; the service's current "
            "World Imagery source record was queried at every sample."
        ),
        "sample_count": len(results),
        "covered_sample_count": len(covered),
        "uncovered_sample_count": len(uncovered),
        "failed_query_count": len(failures),
        "coverage_pct_of_successful_samples": round(100 * len(covered) / max(len(results), 1), 3),
        "providers": sources,
        "products": products,
        "acquisition_dates": dates,
        "native_resolutions_m": resolutions,
        "maximum_cache_levels_reported": max_lods,
        "intersecting_source_records": footprints,
        "failures": failures,
        "samples": results,
    }

    (outdir / "Sabah_Rail_Esri_Imagery_Coverage_Audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    fields = [
        "sample_id",
        "longitude",
        "latitude",
        "covered",
        "provider",
        "product",
        "sensor_or_product",
        "acquisition_date",
        "native_resolution_m",
        "horizontal_accuracy_m",
        "minimum_lod",
        "maximum_lod",
        "block_name",
        "release_name",
    ]
    with (outdir / "Sabah_Rail_Esri_Imagery_Coverage_Samples.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    return audit


def fit_square_bounds(bounds: Iterable[float], size_px: int):
    minx, miny, maxx, maxy = map(float, bounds)
    width = maxx - minx
    height = maxy - miny
    span = max(width, height)
    cx = (minx + maxx) / 2
    cy = (miny + maxy) / 2
    half = span / 2
    return (cx - half, cy - half, cx + half, cy + half), span / size_px


def fetch_export_tile(bounds, size_px: int) -> tuple[Image.Image, dict[str, Any]]:
    minx, miny, maxx, maxy = bounds
    params = {
        "bbox": f"{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f}",
        "bboxSR": "3857",
        "imageSR": "3857",
        "size": f"{size_px},{size_px}",
        "dpi": "96",
        "format": "jpg",
        "transparent": "false",
        "f": "image",
    }
    response = requests.get(
        f"{PUBLIC_SERVICE}/export",
        params=params,
        timeout=180,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if not content_type.lower().startswith("image/"):
        raise RuntimeError(
            f"World Imagery export returned {content_type!r}, not an image: "
            f"{response.text[:500]}"
        )
    image = Image.open(io.BytesIO(response.content)).convert("RGB")
    if image.size != (size_px, size_px):
        raise RuntimeError(f"Unexpected tile size {image.size}; expected {size_px} square")
    stat = ImageStat.Stat(image)
    return image, {
        "bytes": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "mean_rgb": [round(value, 3) for value in stat.mean],
        "variance_rgb": [round(value, 3) for value in stat.var],
        "low_variance": sum(stat.var) < 2.0,
    }


def load_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def add_attribution(image: Image.Image):
    draw = ImageDraw.Draw(image, "RGBA")
    font = load_font(max(18, image.width // 240))
    margin = max(20, image.width // 300)
    bbox = draw.textbbox((0, 0), ATTRIBUTION, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = image.width - text_width - margin
    y = image.height - text_height - margin
    pad = max(10, image.width // 700)
    draw.rounded_rectangle(
        (x - pad, y - pad, x + text_width + pad, y + text_height + pad),
        radius=pad,
        fill=(0, 0, 0, 155),
    )
    draw.text((x, y), ATTRIBUTION, font=font, fill=(255, 255, 255, 230))


def iter_polygon_exteriors(geom):
    if geom.geom_type == "Polygon":
        yield geom.exterior
    elif geom.geom_type == "MultiPolygon":
        for polygon in geom.geoms:
            yield polygon.exterior


def draw_boundary(image: Image.Image, corridor_mercator, bounds):
    minx, miny, maxx, maxy = bounds
    draw = ImageDraw.Draw(image, "RGBA")
    width = max(4, image.width // 1500)
    for exterior in iter_polygon_exteriors(corridor_mercator):
        pixels = []
        for x, y in exterior.coords:
            px = (x - minx) / (maxx - minx) * image.width
            py = (maxy - y) / (maxy - miny) * image.height
            pixels.append((px, py))
        draw.line(pixels, fill=(255, 255, 255, 210), width=width + 3, joint="curve")
        draw.line(pixels, fill=(0, 220, 255, 220), width=width, joint="curve")


def build_presentation(
    context_mercator,
    corridor_wgs84,
    outdir: Path,
    canvas_px: int,
    tile_px: int,
) -> dict[str, Any]:
    if canvas_px % tile_px:
        raise ValueError("Presentation canvas size must be divisible by tile size")
    bounds, pixel_size = fit_square_bounds(context_mercator.bounds, canvas_px)
    minx, miny, maxx, maxy = bounds
    grid = canvas_px // tile_px
    span_x = (maxx - minx) / grid
    span_y = (maxy - miny) / grid
    canvas = Image.new("RGB", (canvas_px, canvas_px))
    tile_requests = []
    for row in range(grid):
        for column in range(grid):
            tile_bounds = (
                minx + column * span_x,
                maxy - (row + 1) * span_y,
                minx + (column + 1) * span_x,
                maxy - row * span_y,
            )
            tile_requests.append((row, column, tile_bounds))

    tiles = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_export_tile, tile_bounds, tile_px):
            (row, column, tile_bounds)
            for row, column, tile_bounds in tile_requests
        }
        for future in as_completed(futures):
            row, column, tile_bounds = futures[future]
            tile, tile_meta = future.result()
            canvas.paste(tile, (column * tile_px, row * tile_px))
            tile_meta.update({"row": row, "column": column, "bounds_epsg3857": tile_bounds})
            tiles.append(tile_meta)
    tiles.sort(key=lambda item: (item["row"], item["column"]))

    main_path = outdir / "Sabah_Rail_HighRes_Satellite_Presentation_8K.png"
    add_attribution(canvas)
    canvas.save(main_path, format="PNG", optimize=True, compress_level=6)

    boundary_canvas = canvas.copy()
    corridor_mercator = transform_geometry(corridor_wgs84, WGS84, WEB_MERCATOR)
    draw_boundary(boundary_canvas, corridor_mercator, bounds)
    add_attribution(boundary_canvas)
    boundary_path = outdir / "Sabah_Rail_HighRes_Satellite_Presentation_8K_Boundary.png"
    boundary_canvas.save(boundary_path, format="PNG", optimize=True, compress_level=6)

    return {
        "service": PUBLIC_SERVICE,
        "operation": "MapServer/export",
        "licensing_class": "static presentation map image",
        "width_px": canvas_px,
        "height_px": canvas_px,
        "pixel_size_web_mercator_m": pixel_size,
        "bounds_epsg3857": list(bounds),
        "tile_request_size_px": tile_px,
        "tile_request_count": len(tiles),
        "tiles": tiles,
        "attribution": ATTRIBUTION,
        "outputs": [main_path.name, boundary_path.name],
    }


def web_mercator_tile_index(x: float, y: float, level: int) -> tuple[int, int]:
    origin = 20037508.342787
    resolution = LOD_RESOLUTIONS[level]
    tile_span = 256 * resolution
    column = math.floor((x + origin) / tile_span)
    row = math.floor((origin - y) / tile_span)
    return column, row


def estimate_aoi_tile_count(aoi_mercator, levels: Iterable[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    minx, miny, maxx, maxy = aoi_mercator.bounds
    for level in levels:
        min_col, max_row = web_mercator_tile_index(minx, miny, level)
        max_col, min_row = web_mercator_tile_index(maxx, maxy, level)
        resolution = LOD_RESOLUTIONS[level]
        tile_span = 256 * resolution
        origin = 20037508.342787
        count = 0
        for row in range(min_row, max_row + 1):
            tile_max_y = origin - row * tile_span
            tile_min_y = tile_max_y - tile_span
            for column in range(min_col, max_col + 1):
                tile_min_x = -origin + column * tile_span
                tile_max_x = tile_min_x + tile_span
                if aoi_mercator.intersects(
                    box(tile_min_x, tile_min_y, tile_max_x, tile_max_y)
                ):
                    count += 1
        counts[str(level)] = count
    return counts


def resolve_arcgis_token() -> tuple[str, str]:
    """Resolve an ArcGIS access token without storing a username/password.

    Supported GitHub-secret combinations, in priority order:
    1. ARCGIS_TOKEN        - short-lived user access token (best for quick trial testing)
    2. ARCGIS_API_KEY      - API key / access token if the connected account permits export
    3. ARCGIS_CLIENT_ID + ARCGIS_REFRESH_TOKEN
                           - OAuth user authentication; refreshes an access token at run time
    4. ARCGIS_CLIENT_ID + ARCGIS_CLIENT_SECRET
                           - OAuth app authentication (not available on ArcGIS Online Trial)
    """
    direct = os.environ.get("ARCGIS_TOKEN", "").strip()
    if direct:
        return direct, "ARCGIS_TOKEN"

    api_key = os.environ.get("ARCGIS_API_KEY", "").strip()
    if api_key:
        return api_key, "ARCGIS_API_KEY"

    client_id = os.environ.get("ARCGIS_CLIENT_ID", "").strip()
    refresh_token = os.environ.get("ARCGIS_REFRESH_TOKEN", "").strip()
    if client_id and refresh_token:
        response = requests.post(
            "https://www.arcgis.com/sharing/rest/oauth2/token/",
            data={
                "f": "json",
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=60,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
        check_error(payload, "ArcGIS OAuth refresh-token exchange")
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError(f"ArcGIS OAuth response did not contain an access token: {payload}")
        return token, "ARCGIS_CLIENT_ID + ARCGIS_REFRESH_TOKEN"

    client_secret = os.environ.get("ARCGIS_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        response = requests.post(
            "https://www.arcgis.com/sharing/rest/oauth2/token/",
            data={
                "f": "json",
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=60,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
        check_error(payload, "ArcGIS OAuth client-credentials exchange")
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError(f"ArcGIS OAuth response did not contain an access token: {payload}")
        return token, "ARCGIS_CLIENT_ID + ARCGIS_CLIENT_SECRET"

    raise RuntimeError(
        "Authenticated World Imagery export requires one of: ARCGIS_TOKEN, "
        "ARCGIS_API_KEY, ARCGIS_CLIENT_ID + ARCGIS_REFRESH_TOKEN, or "
        "ARCGIS_CLIENT_ID + ARCGIS_CLIENT_SECRET."
    )


def token_request(
    session: requests.Session,
    method: str,
    url: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: int = 120,
):
    params = dict(params or {})
    data = dict(data or {})
    if method.upper() == "GET":
        params["token"] = token
    else:
        data["token"] = token
    response = session.request(method, url, params=params, data=data, timeout=timeout)
    response.raise_for_status()
    return response


def check_error(payload: dict[str, Any], action: str):
    error = payload.get("error")
    if error:
        message = error.get("message", "Unknown ArcGIS error")
        details = "; ".join(map(str, error.get("details", [])))
        raise RuntimeError(f"{action} failed: {message}. {details}".strip())


def poll_job(
    session: requests.Session,
    service_url: str,
    job_id: str,
    token: str,
    timeout_minutes: int = 45,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_minutes * 60
    status_url = f"{service_url}/jobs/{job_id}"
    last_status = None
    while time.monotonic() < deadline:
        response = token_request(
            session,
            "GET",
            status_url,
            token,
            params={"f": "json"},
            timeout=120,
        )
        payload = response.json()
        check_error(payload, "ArcGIS export job status")
        status = payload.get("jobStatus")
        if status != last_status:
            print(f"ArcGIS export job status: {status}")
            last_status = status
        if status == "esriJobSucceeded":
            return payload
        if status in {"esriJobFailed", "esriJobCancelled", "esriJobTimedOut"}:
            raise RuntimeError(f"ArcGIS export job ended with {status}: {payload}")
        time.sleep(10)
    raise TimeoutError("ArcGIS export job did not finish within the configured timeout")


def resolve_job_download_url(
    session: requests.Session,
    service_url: str,
    job_id: str,
    job: dict[str, Any],
    token: str,
) -> str:
    results = job.get("results", {})
    if not results:
        raise RuntimeError(f"ArcGIS export job did not report a result parameter: {job}")
    preferred = ["out_service_url", "out_tile_package", "output_file"]
    names = preferred + [name for name in results if name not in preferred]
    for name in names:
        result = results.get(name)
        if result is None:
            continue
        param_url = result.get("paramUrl") if isinstance(result, dict) else None
        if param_url:
            result_url = f"{service_url}/jobs/{job_id}/{param_url.lstrip('/')}"
        else:
            result_url = f"{service_url}/jobs/{job_id}/results/{name}"
        response = token_request(
            session,
            "GET",
            result_url,
            token,
            params={"f": "json"},
            timeout=120,
        )
        payload = response.json()
        check_error(payload, f"ArcGIS result {name}")
        value = payload.get("value")
        if isinstance(value, dict):
            candidate = value.get("url") or value.get("URL")
        else:
            candidate = value
        if isinstance(candidate, str) and candidate.startswith(("https://", "http://")):
            return candidate
    raise RuntimeError("ArcGIS export job completed but no downloadable URL was found")


def export_authorized_tile_package(
    context_mercator,
    outdir: Path,
    api_key: str,
    maximum_level: int,
) -> dict[str, Any]:
    session = http_session()
    root_response = token_request(
        session,
        "GET",
        EXPORT_SERVICE,
        api_key,
        params={"f": "json"},
        timeout=120,
    )
    service_info = root_response.json()
    check_error(service_info, "World Imagery for Export authentication")
    if not service_info.get("exportTilesAllowed"):
        raise RuntimeError("Authenticated service does not report exportTilesAllowed=true")

    compact_v2 = bool(service_info.get("exportTileCacheCompactV2Allowed", True))
    extension = ".tpkx" if compact_v2 else ".tpk"
    storage_format = (
        "esriMapCacheStorageModeCompactV2" if compact_v2
        else "esriMapCacheStorageModeCompact"
    )
    levels = list(range(0, maximum_level + 1))
    counts = estimate_aoi_tile_count(context_mercator, levels)
    estimated_count = sum(counts.values())
    advertised_limit = int(service_info.get("maxExportTilesCount") or 150000)
    if estimated_count > advertised_limit:
        raise RuntimeError(
            f"Buffered corridor is estimated to require {estimated_count:,} tiles, "
            f"above the service limit of {advertised_limit:,}."
        )

    minx, miny, maxx, maxy = context_mercator.bounds
    extent = {
        "xmin": minx,
        "ymin": miny,
        "xmax": maxx,
        "ymax": maxy,
        "spatialReference": {"wkid": 102100, "latestWkid": 3857},
    }
    aoi = {
        "features": [{"geometry": arcgis_polygon(context_mercator, 102100)}],
        "spatialReference": {"wkid": 102100, "latestWkid": 3857},
    }
    params = {
        "f": "json",
        "tilePackage": "true",
        "exportExtent": json.dumps(extent, separators=(",", ":")),
        "optimizeTilesForSize": "true",
        "compressionQuality": "90",
        "exportBy": "levelId",
        "levels": f"0-{maximum_level}",
        "areaOfInterest": json.dumps(aoi, separators=(",", ":")),
        "storageFormatType": storage_format,
    }
    submit = token_request(
        session,
        "POST",
        f"{EXPORT_SERVICE}/exportTiles",
        api_key,
        data=params,
        timeout=180,
    ).json()
    check_error(submit, "World Imagery exportTiles submission")
    job_id = submit.get("jobId")
    if not job_id:
        raise RuntimeError(f"ArcGIS export did not return a jobId: {submit}")
    job = poll_job(session, EXPORT_SERVICE, job_id, api_key)
    download_url = resolve_job_download_url(session, EXPORT_SERVICE, job_id, job, api_key)

    package_path = outdir / f"Sabah_Rail_HighRes_World_Imagery_Level{maximum_level}{extension}"
    with session.get(download_url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with package_path.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    stream.write(chunk)
    if package_path.stat().st_size < 1024:
        raise RuntimeError("Downloaded tile package is unexpectedly small")
    if not zipfile.is_zipfile(package_path):
        raise RuntimeError("Downloaded tile package is not a valid TPK/TPKX archive")

    return {
        "service": EXPORT_SERVICE,
        "item_id": EXPORT_ITEM_ID,
        "item_url": EXPORT_ITEM_URL,
        "authenticated": True,
        "format": extension.lstrip("."),
        "output": package_path.name,
        "bytes": package_path.stat().st_size,
        "sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
        "levels": [0, maximum_level],
        "highest_cache_resolution_web_mercator_m": LOD_RESOLUTIONS[maximum_level],
        "estimated_tiles_by_level": counts,
        "estimated_total_tiles": estimated_count,
        "service_max_export_tiles": advertised_limit,
        "compression_quality": 90,
        "area_of_interest": "study corridor polygon plus configured buffer",
    }


def file_info(path: Path) -> dict[str, Any]:
    info = {"filename": path.name, "bytes": path.stat().st_size}
    if path.suffix.lower() == ".png":
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            info.update({"width_px": image.width, "height_px": image.height, "mode": image.mode})
    return info


def write_limitations(outdir: Path):
    text = f"""# Sabah Rail high-resolution imagery: licence and export limitations

## Authorized source

The authenticated offline source is Esri **World Imagery (for Export)**
({EXPORT_ITEM_URL}). Esri describes it as an export-enabled service for small
volumes of basemap tiles for offline use. It permits up to 150,000 tiles in one
request and requires an ArcGIS Online organizational subscription, an ArcGIS
developer account, or registered-application credentials.

## Output boundary

The official export operation returns a TPK/TPKX offline tile package. It is not
a direct GeoTIFF export. This workflow preserves that authorized output and does
not unpack, scrape, or convert the licensed basemap tiles into a GeoTIFF.
Consequently, the three requested GeoTIFF filenames are intentionally not
created unless Esri exposes a separately authorized direct raster-download
service for the connected account.

The 8K PNG is a static presentation map produced through the official World
Imagery MapServer export operation. It includes this required attribution:

> {ATTRIBUTION}

The 8K image is presentation-only. Its output pixel spacing is coarser than the
native source imagery because the complete buffered corridor must fit in a
finite image. Use the authenticated TPK/TPKX in ArcGIS software for the deepest
authorized offline zoom.
"""
    (outdir / "LICENCE_AND_EXPORT_LIMITATIONS.md").write_text(text, encoding="utf-8")


def write_metadata(
    outdir: Path,
    input_path: Path,
    buffer_m: float,
    corridor_utm,
    context_utm,
    audit: dict[str, Any],
    presentation: dict[str, Any] | None,
    authenticated_export: dict[str, Any] | None,
) -> Path:
    generated_files = []
    for path in sorted(outdir.iterdir()):
        if path.is_file() and path.name not in {
            "Sabah_Rail_HighRes_Satellite_Imagery_Package.zip",
            "Sabah_Rail_HighRes_Satellite_Metadata.json",
        }:
            generated_files.append(file_info(path))
    metadata = {
        "generated_utc": utc_now(),
        "project": "Sabah Rail: Putatan – Kota Kinabalu – KKIP – Sepanggar Port",
        "input_geometry": str(input_path),
        "imagery_provider": "Esri World Imagery",
        "imagery_sources_observed": audit.get("providers", []),
        "imagery_products_observed": audit.get("products", []),
        "acquisition_dates_observed": audit.get("acquisition_dates", []),
        "native_source_resolutions_m_observed": audit.get("native_resolutions_m", []),
        "coverage_audit": {
            key: audit.get(key)
            for key in (
                "method",
                "sample_count",
                "covered_sample_count",
                "uncovered_sample_count",
                "failed_query_count",
                "coverage_pct_of_successful_samples",
                "maximum_cache_levels_reported",
            )
        },
        "spatial": {
            "engineering_crs": TARGET_CRS,
            "web_map_crs": WEB_MERCATOR,
            "wgs84_crs": WGS84,
            "context_buffer_m": buffer_m,
            "corridor_bounds_utm50n": list(map(float, corridor_utm.bounds)),
            "context_bounds_utm50n": list(map(float, context_utm.bounds)),
            "corridor_area_sq_km": corridor_utm.area / 1_000_000,
            "context_area_sq_km": context_utm.area / 1_000_000,
        },
        "presentation_export": presentation,
        "authenticated_offline_export": authenticated_export,
        "geotiff": {
            "created": False,
            "reason": (
                "The verified Esri World Imagery (for Export) service returns an "
                "offline TPK/TPKX tile package, not a direct GeoTIFF. No licensed "
                "tile package conversion was performed."
            ),
            "requested_names_not_created": [
                "Sabah_Rail_HighRes_Satellite_UTM50N.tif",
                "Sabah_Rail_HighRes_Satellite_Corridor_UTM50N.tif",
                "Sabah_Rail_HighRes_Satellite_WGS84.tif",
            ],
        },
        "licence_and_use": {
            "export_item_id": EXPORT_ITEM_ID,
            "export_item_url": EXPORT_ITEM_URL,
            "offline_export_limit": "up to 150,000 tiles per request",
            "static_map_attribution": ATTRIBUTION,
            "limitations_file": "LICENCE_AND_EXPORT_LIMITATIONS.md",
        },
        "files": generated_files,
    }
    metadata_path = outdir / "Sabah_Rail_HighRes_Satellite_Metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata_path


def package_outputs(outdir: Path) -> Path:
    package = outdir / "Sabah_Rail_HighRes_Satellite_Imagery_Package.zip"
    with zipfile.ZipFile(package, "w", allowZip64=True) as archive:
        for path in sorted(outdir.iterdir()):
            if not path.is_file() or path == package:
                continue
            compression = (
                zipfile.ZIP_STORED
                if path.suffix.lower() in {".png", ".tpk", ".tpkx", ".zip"}
                else zipfile.ZIP_DEFLATED
            )
            archive.write(path, arcname=path.name, compress_type=compression)
    if not zipfile.is_zipfile(package):
        raise RuntimeError("Final imagery package is not a valid ZIP archive")
    return package


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="data/Sabah_Rail_Study_Area_and_Alignment.geojson",
    )
    parser.add_argument(
        "--output",
        default="output/sabah_rail_esri_hires",
    )
    parser.add_argument(
        "--mode",
        choices=("audit", "presentation", "export", "all"),
        default="all",
    )
    parser.add_argument("--buffer-m", type=float, default=3000.0)
    parser.add_argument("--audit-grid-m", type=float, default=4000.0)
    parser.add_argument("--presentation-px", type=int, default=8192)
    parser.add_argument("--request-tile-px", type=int, default=2048)
    parser.add_argument("--maximum-level", type=int, default=19)
    parser.add_argument("--reuse-audit", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    _, corridor_wgs84, alignment_wgs84 = load_project_geometry(input_path)
    corridor_utm, context_utm, context_wgs84, context_mercator = build_context(
        corridor_wgs84, args.buffer_m
    )

    audit_path = outdir / "Sabah_Rail_Esri_Imagery_Coverage_Audit.json"
    if args.reuse_audit and audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    else:
        audit = run_coverage_audit(
            alignment_wgs84,
            context_utm,
            context_wgs84,
            outdir,
            args.audit_grid_m,
        )
    if audit["failed_query_count"]:
        raise RuntimeError(
            f"Coverage audit had {audit['failed_query_count']} failed queries; see audit JSON"
        )
    if audit["uncovered_sample_count"]:
        raise RuntimeError(
            f"Coverage audit found {audit['uncovered_sample_count']} uncovered samples"
        )

    presentation = None
    authenticated_export = None
    if args.mode in {"presentation", "all"}:
        presentation = build_presentation(
            context_mercator,
            corridor_wgs84,
            outdir,
            args.presentation_px,
            args.request_tile_px,
        )

    if args.mode in {"export", "all"}:
        access_token, auth_method = resolve_arcgis_token()
        print(f"ArcGIS authentication resolved via {auth_method}.")
        authenticated_export = export_authorized_tile_package(
            context_mercator,
            outdir,
            access_token,
            args.maximum_level,
        )
        authenticated_export["authentication_method"] = auth_method

    write_limitations(outdir)
    metadata_path = write_metadata(
        outdir,
        input_path,
        args.buffer_m,
        corridor_utm,
        context_utm,
        audit,
        presentation,
        authenticated_export,
    )
    package_path = package_outputs(outdir)
    print(
        json.dumps(
            {
                "metadata": str(metadata_path),
                "package": str(package_path),
                "presentation": presentation,
                "authenticated_export": authenticated_export,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
