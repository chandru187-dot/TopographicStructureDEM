#!/usr/bin/env python3
"""Cloud-first Esri World Imagery inspection and authorized offline export.

Designed for GitHub Actions: no ArcGIS Pro, Windows workstation, or ArcPy is
required. The export path uses Esri's export-enabled World Imagery service and
preserves the returned TPK/TPKX package; it does not scrape tiles or convert
licensed basemap content into an unrestricted GeoTIFF.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from pyproj import CRS, Transformer
from shapely.geometry import Point, box, shape
from shapely.geometry.polygon import orient
from shapely.ops import transform as shp_transform, unary_union


PUBLIC_SERVICE = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer"
EXPORT_SERVICE = "https://tiledbasemaps.arcgis.com/arcgis/rest/services/World_Imagery/MapServer"
WGS84 = "EPSG:4326"
WEB_MERCATOR = "EPSG:3857"
LOD_RESOLUTIONS = [156543.03392800014 / (2**level) for level in range(24)]
USER_AGENT = "CloudEngineeringGeoEngine/1.0"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def session() -> requests.Session:
    s = requests.Session()
    headers = {"User-Agent": USER_AGENT}
    referer = os.environ.get("ARCGIS_REFERER", "").strip()
    if referer:
        headers["Referer"] = referer
    s.headers.update(headers)
    s.mount("https://", requests.adapters.HTTPAdapter(max_retries=3))
    return s


def check_error(payload: dict[str, Any], action: str) -> None:
    if payload.get("error"):
        err = payload["error"]
        msg = err.get("message", "Unknown ArcGIS error")
        details = "; ".join(map(str, err.get("details", [])))
        raise RuntimeError(f"{action}: {msg}. {details}".strip())


def resolve_arcgis_token() -> tuple[str, str]:
    direct = os.environ.get("ARCGIS_TOKEN", "").strip()
    if direct:
        return direct, "ARCGIS_TOKEN"

    api_key = os.environ.get("ARCGIS_API_KEY", "").strip()
    if api_key:
        return api_key, "ARCGIS_API_KEY"

    client_id = os.environ.get("ARCGIS_CLIENT_ID", "").strip()
    refresh_token = os.environ.get("ARCGIS_REFRESH_TOKEN", "").strip()
    if client_id and refresh_token:
        r = requests.post(
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
        r.raise_for_status()
        payload = r.json()
        check_error(payload, "OAuth refresh-token exchange")
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("OAuth refresh-token exchange returned no access token")
        return token, "ARCGIS_CLIENT_ID + ARCGIS_REFRESH_TOKEN"

    client_secret = os.environ.get("ARCGIS_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        r = requests.post(
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
        r.raise_for_status()
        payload = r.json()
        check_error(payload, "OAuth client-credentials exchange")
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("OAuth client-credentials exchange returned no access token")
        return token, "ARCGIS_CLIENT_ID + ARCGIS_CLIENT_SECRET"

    raise RuntimeError(
        "No ArcGIS export credential is configured. Use ARCGIS_TOKEN for the "
        "trial test, or OAuth credentials for a persistent setup."
    )


def validate_portal_token(token: str) -> dict[str, Any]:
    """Validate the token against ArcGIS Online without exposing it."""
    s = session()
    r = s.get(
        "https://www.arcgis.com/sharing/rest/portals/self",
        params={"f": "json", "token": token},
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    check_error(payload, "ArcGIS Online portal token validation")
    return {
        "id": payload.get("id"),
        "name": payload.get("name"),
        "urlKey": payload.get("urlKey"),
        "customBaseUrl": payload.get("customBaseUrl"),
    }


def exchange_server_token(portal_token: str) -> str:
    """Try the documented portal-token -> server-token exchange."""
    s = session()
    r = s.post(
        "https://www.arcgis.com/sharing/rest/generateToken",
        data={
            "f": "json",
            "token": portal_token,
            "serverURL": "https://tiledbasemaps.arcgis.com/arcgis",
        },
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    check_error(payload, "ArcGIS server-token exchange")
    token = str(payload.get("token") or "").strip()
    if not token:
        raise RuntimeError("ArcGIS server-token exchange returned no token")
    return token


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = ["project_name", "geometry_path"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"Missing config values: {', '.join(missing)}")
    return data


def load_aoi(geojson_path: Path, additional_buffer_m: float):
    fc = json.loads(geojson_path.read_text(encoding="utf-8"))
    features = fc.get("features", [])
    geoms = [shape(f["geometry"]) for f in features if f.get("geometry")]
    if not geoms:
        raise RuntimeError("No usable geometry found in GeoJSON")

    polygons = [g for g in geoms if g.geom_type in {"Polygon", "MultiPolygon"}]
    lines = [g for g in geoms if g.geom_type in {"LineString", "MultiLineString"}]
    points = [g for g in geoms if g.geom_type in {"Point", "MultiPoint"}]

    if polygons:
        base = unary_union(polygons)
        source_kind = "polygon"
    elif lines:
        base = unary_union(lines)
        source_kind = "line"
    elif points:
        base = unary_union(points)
        source_kind = "point"
    else:
        raise RuntimeError("GeoJSON contains no polygon, line, or point geometry")

    centroid = base.centroid
    lon, lat = centroid.x, centroid.y
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise RuntimeError("Input geometry is expected to be WGS84 longitude/latitude")

    zone = max(1, min(60, int(math.floor((lon + 180) / 6) + 1)))
    epsg = (32600 if lat >= 0 else 32700) + zone
    local_crs = CRS.from_epsg(epsg)
    to_local = Transformer.from_crs(WGS84, local_crs, always_xy=True)
    to_wgs = Transformer.from_crs(local_crs, WGS84, always_xy=True)
    local = shp_transform(to_local.transform, base)

    if source_kind in {"line", "point"}:
        if additional_buffer_m <= 0:
            raise RuntimeError(
                "A line/point-only input requires additional_buffer_m > 0 to create an export area"
            )
        local = local.buffer(additional_buffer_m)
    elif additional_buffer_m:
        local = local.buffer(additional_buffer_m)

    aoi_wgs = shp_transform(to_wgs.transform, local)
    to_merc = Transformer.from_crs(WGS84, WEB_MERCATOR, always_xy=True)
    aoi_merc = shp_transform(to_merc.transform, aoi_wgs)
    return aoi_wgs, aoi_merc, f"EPSG:{epsg}", source_kind


def representative_points(aoi_wgs, grid: int = 5) -> list[Point]:
    minx, miny, maxx, maxy = aoi_wgs.bounds
    pts: list[Point] = [aoi_wgs.representative_point()]
    if grid > 1:
        for ix in range(grid):
            for iy in range(grid):
                x = minx + (ix + 0.5) * (maxx - minx) / grid
                y = miny + (iy + 0.5) * (maxy - miny) / grid
                p = Point(x, y)
                if aoi_wgs.covers(p):
                    pts.append(p)
    unique = {}
    for p in pts:
        unique[(round(p.x, 7), round(p.y, 7))] = p
    return list(unique.values())


def query_source_at_point(p: Point) -> dict[str, Any]:
    params = {
        "f": "json",
        "where": "1=1",
        "geometry": f"{p.x},{p.y}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outFields": (
            "SRC_DATE,SRC_RES,SRC_ACC,SRC_DESC,NICE_NAME,NICE_DESC,"
            "MinMapLevel,MaxMapLevel,BlockName,ReleaseName"
        ),
        "returnGeometry": "false",
    }
    r = requests.get(f"{PUBLIC_SERVICE}/0/query", params=params, timeout=60, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    payload = r.json()
    check_error(payload, "World Imagery source query")
    fs = payload.get("features", [])
    result = {"longitude": p.x, "latitude": p.y, "covered": bool(fs)}
    if fs:
        result.update(fs[0].get("attributes", {}))
    return result


def inspect_imagery(aoi_wgs) -> dict[str, Any]:
    samples = []
    failures = []
    for p in representative_points(aoi_wgs):
        try:
            samples.append(query_source_at_point(p))
        except Exception as exc:
            failures.append({"longitude": p.x, "latitude": p.y, "error": str(exc)})

    resolutions = sorted(
        {float(s["SRC_RES"]) for s in samples if s.get("covered") and s.get("SRC_RES") is not None}
    )
    dates = sorted({str(s["SRC_DATE"]) for s in samples if s.get("SRC_DATE")})
    providers = sorted(
        {str(s.get("NICE_DESC") or s.get("SRC_DESC")) for s in samples if s.get("NICE_DESC") or s.get("SRC_DESC")}
    )
    max_levels = sorted(
        {int(s["MaxMapLevel"]) for s in samples if s.get("MaxMapLevel") is not None}
    )
    return {
        "checked_utc": now_utc(),
        "service": PUBLIC_SERVICE,
        "sample_count": len(samples),
        "failure_count": len(failures),
        "native_resolutions_m_observed": resolutions,
        "source_dates_observed": dates,
        "providers_observed": providers,
        "maximum_cache_levels_observed": max_levels,
        "samples": samples,
        "failures": failures,
    }


def arcgis_polygon(geom, wkid: int) -> dict[str, Any]:
    polygons = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    rings = []
    for polygon in polygons:
        polygon = orient(polygon, sign=-1.0)
        rings.append([[float(x), float(y)] for x, y in polygon.exterior.coords])
        for interior in polygon.interiors:
            rings.append([[float(x), float(y)] for x, y in interior.coords])
    return {"rings": rings, "spatialReference": {"wkid": wkid}}


def tile_index(x: float, y: float, level: int) -> tuple[int, int]:
    origin = 20037508.342787
    tile_span = 256 * LOD_RESOLUTIONS[level]
    col = math.floor((x + origin) / tile_span)
    row = math.floor((origin - y) / tile_span)
    return col, row


def bbox_tile_count(bounds, max_level: int) -> int:
    minx, miny, maxx, maxy = bounds
    total = 0
    for level in range(max_level + 1):
        min_col, max_row = tile_index(minx, miny, level)
        max_col, min_row = tile_index(maxx, maxy, level)
        total += max(0, max_col - min_col + 1) * max(0, max_row - min_row + 1)
    return total


def split_for_limit(geom, max_level: int, limit: int, depth: int = 0) -> list:
    count = bbox_tile_count(geom.bounds, max_level)
    if count <= limit:
        return [geom]
    if depth >= 20:
        raise RuntimeError(f"Unable to split export below tile limit; estimated tiles={count:,}")

    minx, miny, maxx, maxy = geom.bounds
    if (maxx - minx) >= (maxy - miny):
        mid = (minx + maxx) / 2
        boxes = [box(minx, miny, mid, maxy), box(mid, miny, maxx, maxy)]
    else:
        mid = (miny + maxy) / 2
        boxes = [box(minx, miny, maxx, mid), box(minx, mid, maxx, maxy)]

    parts = []
    for b in boxes:
        p = geom.intersection(b)
        if not p.is_empty and p.area > 0:
            parts.extend(split_for_limit(p, max_level, limit, depth + 1))
    return parts


def token_get(s: requests.Session, url: str, token: str, params=None, timeout=120):
    q = dict(params or {})
    q["token"] = token
    r = s.get(url, params=q, timeout=timeout)
    r.raise_for_status()
    return r


def token_post(s: requests.Session, url: str, token: str, data=None, timeout=180):
    body = dict(data or {})
    body["token"] = token
    r = s.post(url, data=body, timeout=timeout)
    r.raise_for_status()
    return r


def poll_job(s: requests.Session, job_id: str, token: str, timeout_minutes: int = 60) -> dict[str, Any]:
    url = f"{EXPORT_SERVICE}/jobs/{job_id}"
    deadline = time.monotonic() + timeout_minutes * 60
    while time.monotonic() < deadline:
        payload = token_get(s, url, token, {"f": "json"}).json()
        check_error(payload, "Export job status")
        status = payload.get("jobStatus")
        print(f"Job {job_id}: {status}")
        if status == "esriJobSucceeded":
            return payload
        if status in {"esriJobFailed", "esriJobCancelled", "esriJobTimedOut"}:
            raise RuntimeError(f"Export job ended with {status}: {payload}")
        time.sleep(8)
    raise TimeoutError(f"Export job {job_id} timed out")


def result_url(s: requests.Session, job_id: str, job: dict[str, Any], token: str) -> str:
    direct = job.get("outputUrl")
    if isinstance(direct, str) and direct.startswith("http"):
        return direct

    results = job.get("results") or {}
    preferred = ["out_service_url", "out_tile_package", "output_file"]
    for name in preferred + [n for n in results if n not in preferred]:
        entry = results.get(name)
        if entry is None:
            continue
        param_url = entry.get("paramUrl") if isinstance(entry, dict) else None
        endpoint = (
            f"{EXPORT_SERVICE}/jobs/{job_id}/{param_url.lstrip('/')}"
            if param_url
            else f"{EXPORT_SERVICE}/jobs/{job_id}/results/{name}"
        )
        payload = token_get(s, endpoint, token, {"f": "json"}).json()
        check_error(payload, f"Export result {name}")
        value = payload.get("value")
        candidate = value.get("url") if isinstance(value, dict) else value
        if isinstance(candidate, str) and candidate.startswith("http"):
            return candidate
    raise RuntimeError("Export job succeeded but no download URL was returned")


def download_package(s: requests.Session, url: str, token: str, destination: Path) -> None:
    # Esri export-job result URLs can return an HTTP-200 JSON token error when
    # fetched without authentication. Always attach the token for ArcGIS-hosted
    # result URLs instead of waiting for an HTTP 401/403 response.
    hostname = (urlparse(url).hostname or "").lower()
    params = {"token": token} if ("arcgis.com" in hostname or "arcgisonline.com" in hostname) else None
    with s.get(url, params=params, stream=True, timeout=600) as r:
        r.raise_for_status()
        content_type = (r.headers.get("Content-Type") or "").lower()
        with destination.open("wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)

    if destination.stat().st_size < 1024 or not zipfile.is_zipfile(destination):
        preview = destination.read_bytes()[:500]
        try:
            preview_text = preview.decode("utf-8", errors="replace")
        except Exception:
            preview_text = repr(preview)
        raise RuntimeError(
            "Downloaded package is invalid or unexpectedly small: "
            f"{destination}; content-type={content_type!r}; "
            f"response-preview={preview_text!r}"
        )


def export_imagery(aoi_merc, outdir: Path, max_level: int) -> dict[str, Any]:
    token, auth_method = resolve_arcgis_token()
    portal = validate_portal_token(token)
    print(
        "ArcGIS Online token validated for organization: "
        + str(portal.get("name") or portal.get("urlKey") or portal.get("id") or "unknown")
    )

    s = session()
    info = token_get(s, EXPORT_SERVICE, token, {"f": "json"}).json()
    if info.get("error"):
        message = str(info.get("error", {}).get("message", ""))
        if "invalid token" in message.lower():
            print("Portal token was not accepted by tiledbasemaps; attempting server-token exchange.")
            token = exchange_server_token(token)
            auth_method += " -> server token"
            info = token_get(s, EXPORT_SERVICE, token, {"f": "json"}).json()
    check_error(info, "World Imagery for Export authentication")
    if not info.get("exportTilesAllowed"):
        raise RuntimeError("The authenticated World Imagery service does not allow exportTiles")

    service_limit = int(info.get("maxExportTilesCount") or 150000)
    safe_limit = max(1000, int(service_limit * 0.90))
    compact_v2 = bool(info.get("exportTileCacheCompactV2Allowed", True))
    extension = ".tpkx" if compact_v2 else ".tpk"
    storage = "esriMapCacheStorageModeCompactV2" if compact_v2 else "esriMapCacheStorageModeCompact"

    parts = split_for_limit(aoi_merc, max_level, safe_limit)
    exported = []
    for index, part in enumerate(parts, start=1):
        minx, miny, maxx, maxy = map(float, part.bounds)
        extent = {
            "xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
            "spatialReference": {"wkid": 102100, "latestWkid": 3857},
        }
        aoi = {
            "features": [{"geometry": arcgis_polygon(part, 102100)}],
            "spatialReference": {"wkid": 102100, "latestWkid": 3857},
        }
        body = {
            "f": "json",
            "tilePackage": "true",
            "exportExtent": json.dumps(extent, separators=(",", ":")),
            "optimizeTilesForSize": "true",
            "compressionQuality": "90",
            "exportBy": "levelId",
            "levels": f"0-{max_level}",
            "areaOfInterest": json.dumps(aoi, separators=(",", ":")),
            "storageFormatType": storage,
        }
        submitted = token_post(s, f"{EXPORT_SERVICE}/exportTiles", token, body).json()
        check_error(submitted, f"Submit export part {index}")
        job_id = submitted.get("jobId")
        if not job_id:
            raise RuntimeError(f"No jobId returned for export part {index}: {submitted}")
        job = poll_job(s, job_id, token)
        url = result_url(s, job_id, job, token)
        path = outdir / f"world_imagery_part_{index:03d}_L{max_level}{extension}"
        download_package(s, url, token, path)
        exported.append({
            "part": index,
            "job_id": job_id,
            "file": path.name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bounds_epsg3857": [minx, miny, maxx, maxy],
            "estimated_bbox_tiles": bbox_tile_count(part.bounds, max_level),
        })

    return {
        "service": EXPORT_SERVICE,
        "authentication_method": auth_method,
        "maximum_level": max_level,
        "nominal_web_mercator_resolution_m_at_max_level": LOD_RESOLUTIONS[max_level],
        "service_max_export_tiles": service_limit,
        "safe_chunk_limit": safe_limit,
        "chunk_count": len(parts),
        "packages": exported,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/esri_cloud_request.json")
    parser.add_argument("--mode", choices=("inspect", "export"), default=None)
    args = parser.parse_args()

    config_path = Path(args.config)
    cfg = load_config(config_path)
    mode = args.mode or cfg.get("requested_mode", "inspect")
    geometry_path = Path(cfg["geometry_path"])
    outdir = Path(cfg.get("output_dir", "output/esri_cloud_imagery"))
    outdir.mkdir(parents=True, exist_ok=True)

    aoi_wgs, aoi_merc, local_crs, source_kind = load_aoi(
        geometry_path,
        float(cfg.get("additional_buffer_m", 0.0)),
    )
    inspection = inspect_imagery(aoi_wgs)
    (outdir / "imagery_inspection.json").write_text(
        json.dumps(inspection, indent=2), encoding="utf-8"
    )

    export = None
    if mode == "export":
        export = export_imagery(
            aoi_merc,
            outdir,
            int(cfg.get("maximum_level", 19)),
        )

    manifest = {
        "generated_utc": now_utc(),
        "project_name": cfg["project_name"],
        "requested_mode": cfg.get("requested_mode", "inspect"),
        "effective_mode": mode,
        "geometry_path": str(geometry_path),
        "geometry_source_kind": source_kind,
        "local_processing_crs": local_crs,
        "additional_buffer_m": float(cfg.get("additional_buffer_m", 0.0)),
        "aoi_bounds_wgs84": list(map(float, aoi_wgs.bounds)),
        "aoi_area_sq_km_web_mercator_approx": float(aoi_merc.area / 1_000_000),
        "inspection": {
            "native_resolutions_m_observed": inspection["native_resolutions_m_observed"],
            "source_dates_observed": inspection["source_dates_observed"],
            "providers_observed": inspection["providers_observed"],
            "maximum_cache_levels_observed": inspection["maximum_cache_levels_observed"],
            "sample_count": inspection["sample_count"],
            "failure_count": inspection["failure_count"],
        },
        "authorized_export": export,
        "licensing_note": (
            "Authorized Esri offline export is preserved as TPK/TPKX. "
            "This workflow does not scrape basemap tiles or convert the package "
            "to an unrestricted GeoTIFF."
        ),
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
