#!/usr/bin/env python3
"""Create presentation-ready Esri World Imagery images from a project GeoJSON.

This uses the official World Imagery MapServer /export operation for static map
images. It does not scrape cache tiles and does not unpack the licensed TPKX.
Outputs are intended for project viewing, presentation, and engineering context.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable

import requests
from PIL import Image, ImageDraw, ImageFont
from pyproj import CRS, Transformer
from shapely.geometry import box, shape
from shapely.ops import transform as shp_transform, unary_union


PUBLIC_SERVICE = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer"
WGS84 = "EPSG:4326"
WEB_MERCATOR = "EPSG:3857"
ATTRIBUTION = "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community"
USER_AGENT = "CloudEngineeringGeoEngine-Presentation/1.0"


def load_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def add_attribution(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    font = load_font(max(16, min(30, image.width // 180)))
    pad = max(8, image.width // 700)
    margin = max(12, image.width // 500)
    bbox = draw.textbbox((0, 0), ATTRIBUTION, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(margin, image.width - tw - margin)
    y = max(margin, image.height - th - margin)
    draw.rounded_rectangle(
        (x - pad, y - pad, x + tw + pad, y + th + pad),
        radius=pad,
        fill=(0, 0, 0, 145),
    )
    draw.text((x, y), ATTRIBUTION, font=font, fill=(255, 255, 255, 235))


def fetch_export(bounds, width: int, height: int) -> Image.Image:
    minx, miny, maxx, maxy = map(float, bounds)
    params = {
        "bbox": f"{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f}",
        "bboxSR": "3857",
        "imageSR": "3857",
        "size": f"{width},{height}",
        "dpi": "96",
        "format": "jpg",
        "transparent": "false",
        "f": "image",
    }
    r = requests.get(
        f"{PUBLIC_SERVICE}/export",
        params=params,
        timeout=180,
        headers={"User-Agent": USER_AGENT},
    )
    r.raise_for_status()
    ctype = (r.headers.get("Content-Type") or "").lower()
    if not ctype.startswith("image/"):
        raise RuntimeError(f"World Imagery export returned {ctype!r}: {r.text[:500]}")
    from io import BytesIO
    return Image.open(BytesIO(r.content)).convert("RGB")


def transform_geom(geom, src, dst):
    t = Transformer.from_crs(src, dst, always_xy=True)
    return shp_transform(t.transform, geom)


def load_project(path: Path):
    fc = json.loads(path.read_text(encoding="utf-8"))
    geoms = [(f.get("properties", {}), shape(f["geometry"])) for f in fc.get("features", []) if f.get("geometry")]
    polygons = [g for _, g in geoms if g.geom_type in {"Polygon", "MultiPolygon"}]
    lines = [g for _, g in geoms if g.geom_type in {"LineString", "MultiLineString"}]
    if not polygons:
        raise RuntimeError("Project GeoJSON needs at least one polygon for the study area")
    if not lines:
        raise RuntimeError("Project GeoJSON needs at least one line for the alignment")
    return unary_union(polygons), unary_union(lines)


def project_context(study_wgs, alignment_wgs):
    c = study_wgs.centroid
    zone = max(1, min(60, int(math.floor((c.x + 180) / 6) + 1)))
    epsg = (32600 if c.y >= 0 else 32700) + zone
    local = CRS.from_epsg(epsg)
    study_local = transform_geom(study_wgs, WGS84, local)
    alignment_local = transform_geom(alignment_wgs, WGS84, local)
    study_merc = transform_geom(study_wgs, WGS84, WEB_MERCATOR)
    alignment_merc = transform_geom(alignment_wgs, WGS84, WEB_MERCATOR)
    return local, study_local, alignment_local, study_merc, alignment_merc


def geom_lines(geom):
    if geom.geom_type == "LineString":
        yield geom
    elif geom.geom_type == "MultiLineString":
        yield from geom.geoms
    elif geom.geom_type == "Polygon":
        yield geom.exterior
        for ring in geom.interiors:
            yield ring
    elif geom.geom_type == "MultiPolygon":
        for p in geom.geoms:
            yield p.exterior
            for ring in p.interiors:
                yield ring


def to_pixels(coords: Iterable[tuple[float, float]], bounds, width: int, height: int):
    minx, miny, maxx, maxy = bounds
    for x, y in coords:
        px = (x - minx) / (maxx - minx) * width
        py = (maxy - y) / (maxy - miny) * height
        yield (px, py)


def draw_project_overlay(
    image: Image.Image,
    bounds,
    study_merc,
    alignment_merc,
    *,
    draw_sheet_boxes=None,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    width = max(2, image.width // 1400)

    for g in geom_lines(study_merc):
        pts = list(to_pixels(g.coords, bounds, image.width, image.height))
        if len(pts) > 1:
            draw.line(pts, fill=(255, 255, 255, 225), width=width + 3)
            draw.line(pts, fill=(0, 220, 255, 235), width=width)

    for g in geom_lines(alignment_merc):
        pts = list(to_pixels(g.coords, bounds, image.width, image.height))
        if len(pts) > 1:
            draw.line(pts, fill=(255, 255, 255, 235), width=width + 4)
            draw.line(pts, fill=(230, 35, 35, 245), width=width + 1)

    if draw_sheet_boxes:
        font = load_font(max(18, image.width // 260))
        for rec in draw_sheet_boxes:
            b = rec["bounds_epsg3857"]
            x0, y1 = next(to_pixels([(b[0], b[1])], bounds, image.width, image.height))
            x1, y0 = next(to_pixels([(b[2], b[3])], bounds, image.width, image.height))
            draw.rectangle((x0, y0, x1, y1), outline=(255, 220, 0, 225), width=max(2, width))
            label = f"{rec['sheet']:02d}"
            draw.text((x0 + 4, y0 + 2), label, font=font, fill=(255, 255, 255, 255),
                      stroke_width=2, stroke_fill=(0, 0, 0, 210))


def tiled_overview(bounds, long_side_px: int, max_request_px: int):
    minx, miny, maxx, maxy = map(float, bounds)
    spanx, spany = maxx - minx, maxy - miny
    if spanx >= spany:
        width = long_side_px
        height = max(1024, int(round(long_side_px * spany / spanx)))
    else:
        height = long_side_px
        width = max(1024, int(round(long_side_px * spanx / spany)))

    cols = max(1, math.ceil(width / max_request_px))
    rows = max(1, math.ceil(height / max_request_px))
    tile_w = math.ceil(width / cols)
    tile_h = math.ceil(height / rows)

    canvas = Image.new("RGB", (width, height))
    for row in range(rows):
        py0 = row * tile_h
        py1 = min(height, (row + 1) * tile_h)
        h = py1 - py0
        y_top = maxy - (py0 / height) * spany
        y_bottom = maxy - (py1 / height) * spany
        for col in range(cols):
            px0 = col * tile_w
            px1 = min(width, (col + 1) * tile_w)
            w = px1 - px0
            x_left = minx + (px0 / width) * spanx
            x_right = minx + (px1 / width) * spanx
            tile = fetch_export((x_left, y_bottom, x_right, y_top), w, h)
            canvas.paste(tile, (px0, py0))
    return canvas, [minx, miny, maxx, maxy]


def sheet_centers(alignment_local, step_m: float):
    lines = list(alignment_local.geoms) if alignment_local.geom_type == "MultiLineString" else [alignment_local]
    records = []
    sheet_no = 1
    for line in lines:
        length = float(line.length)
        d = 0.0
        while d < length:
            p = line.interpolate(min(d + step_m / 2, length))
            records.append((sheet_no, p, min(d, length), min(d + step_m, length)))
            sheet_no += 1
            d += step_m
    return records


def build_detail_sheets(
    alignment_local,
    study_merc,
    alignment_merc,
    local_crs,
    outdir: Path,
    *,
    step_m: float,
    extent_m: float,
    pixels: int,
):
    to_merc = Transformer.from_crs(local_crs, WEB_MERCATOR, always_xy=True)
    records = []
    clean_dir = outdir / "detail_clean"
    overlay_dir = outdir / "detail_with_alignment"
    clean_dir.mkdir(parents=True, exist_ok=True)
    overlay_dir.mkdir(parents=True, exist_ok=True)

    half = extent_m / 2
    for sheet_no, p, ch_start, ch_end in sheet_centers(alignment_local, step_m):
        local_box = box(p.x - half, p.y - half, p.x + half, p.y + half)
        merc_box = shp_transform(to_merc.transform, local_box)
        bounds = list(map(float, merc_box.bounds))

        clean = fetch_export(bounds, pixels, pixels)
        add_attribution(clean)
        clean_path = clean_dir / f"Sabah_Rail_Detail_{sheet_no:02d}_clean.jpg"
        clean.save(clean_path, "JPEG", quality=94, subsampling=0, optimize=True)

        overlay = clean.copy()
        draw_project_overlay(overlay, bounds, study_merc, alignment_merc)
        add_attribution(overlay)
        overlay_path = overlay_dir / f"Sabah_Rail_Detail_{sheet_no:02d}_alignment.jpg"
        overlay.save(overlay_path, "JPEG", quality=94, subsampling=0, optimize=True)

        center_x, center_y = to_merc.transform(p.x, p.y)
        records.append({
            "sheet": sheet_no,
            "chainage_start_m_approx": round(ch_start, 1),
            "chainage_end_m_approx": round(ch_end, 1),
            "center_epsg3857": [center_x, center_y],
            "bounds_epsg3857": bounds,
            "ground_extent_m_local": extent_m,
            "pixel_width": pixels,
            "nominal_local_ground_pixel_m": extent_m / pixels,
            "clean_image": str(clean_path.relative_to(outdir)),
            "alignment_image": str(overlay_path.relative_to(outdir)),
        })
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/esri_cloud_request.json")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    img_cfg = cfg.get("presentation_images", {})
    if not img_cfg.get("enabled", True):
        print("Presentation imagery disabled in config.")
        return

    geometry_path = Path(cfg["geometry_path"])
    outdir = Path(cfg.get("output_dir", "output/esri_cloud_imagery")) / "presentation_images"
    outdir.mkdir(parents=True, exist_ok=True)

    study_wgs, alignment_wgs = load_project(geometry_path)
    local_crs, study_local, alignment_local, study_merc, alignment_merc = project_context(
        study_wgs, alignment_wgs
    )

    overview_padding_m = float(img_cfg.get("overview_padding_m", 1000))
    padded_local = study_local.buffer(overview_padding_m)
    padded_merc = transform_geom(padded_local, local_crs, WEB_MERCATOR)

    overview, overview_bounds = tiled_overview(
        padded_merc.bounds,
        int(img_cfg.get("overview_long_side_px", 8192)),
        int(img_cfg.get("max_export_request_px", 2048)),
    )
    add_attribution(overview)
    clean_overview = outdir / "Sabah_Rail_Satellite_Overall.jpg"
    overview.save(clean_overview, "JPEG", quality=94, subsampling=0, optimize=True)

    overview_overlay = overview.copy()
    draw_project_overlay(overview_overlay, overview_bounds, study_merc, alignment_merc)
    add_attribution(overview_overlay)
    overlay_path = outdir / "Sabah_Rail_Satellite_Overall_With_Alignment.jpg"
    overview_overlay.save(overlay_path, "JPEG", quality=94, subsampling=0, optimize=True)

    records = build_detail_sheets(
        alignment_local,
        study_merc,
        alignment_merc,
        local_crs,
        outdir,
        step_m=float(img_cfg.get("detail_sheet_step_m", 1000)),
        extent_m=float(img_cfg.get("detail_sheet_extent_m", 1200)),
        pixels=int(img_cfg.get("detail_sheet_px", 2048)),
    )

    index = overview.copy()
    draw_project_overlay(
        index,
        overview_bounds,
        study_merc,
        alignment_merc,
        draw_sheet_boxes=records,
    )
    add_attribution(index)
    index_path = outdir / "Sabah_Rail_Satellite_Detail_Sheet_Index.jpg"
    index.save(index_path, "JPEG", quality=94, subsampling=0, optimize=True)

    manifest = {
        "project_name": cfg.get("project_name"),
        "geometry_path": str(geometry_path),
        "static_imagery_service": PUBLIC_SERVICE,
        "usage": "presentation and engineering context images",
        "local_crs": local_crs.to_string(),
        "overview": {
            "clean": clean_overview.name,
            "with_alignment": overlay_path.name,
            "sheet_index": index_path.name,
            "width_px": overview.width,
            "height_px": overview.height,
            "bounds_epsg3857": overview_bounds,
        },
        "detail_sheet_count": len(records),
        "detail_sheet_step_m": float(img_cfg.get("detail_sheet_step_m", 1000)),
        "detail_sheet_extent_m": float(img_cfg.get("detail_sheet_extent_m", 1200)),
        "detail_sheet_px": int(img_cfg.get("detail_sheet_px", 2048)),
        "nominal_detail_ground_pixel_m": (
            float(img_cfg.get("detail_sheet_extent_m", 1200))
            / int(img_cfg.get("detail_sheet_px", 2048))
        ),
        "sheets": records,
        "attribution": ATTRIBUTION,
        "note": (
            "These are static presentation images from the official World Imagery "
            "MapServer export operation. The separate authorized offline imagery "
            "package remains in TPKX format."
        ),
    }
    (outdir / "presentation_images_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "overview": clean_overview.name,
        "overview_with_alignment": overlay_path.name,
        "sheet_index": index_path.name,
        "detail_sheet_count": len(records),
        "nominal_detail_ground_pixel_m": manifest["nominal_detail_ground_pixel_m"],
    }, indent=2))


if __name__ == "__main__":
    main()
