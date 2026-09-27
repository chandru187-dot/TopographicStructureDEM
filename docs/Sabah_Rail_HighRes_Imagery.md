# Sabah Rail authorized high-resolution imagery

This workflow builds presentation imagery and, when authenticated, requests an
offline tile package from Esri's official **World Imagery (for Export)** service.

## Credential

Configure the GitHub Actions repository secret `ARCGIS_API_KEY`. The key must
belong to an ArcGIS account allowed to access the export-enabled World Imagery
service. The workflow never prints the key or commits it to the repository.

## Run

Open **Actions → Build Sabah Rail Authorized Esri Imagery → Run workflow** on
the `sabah-rail-hires-imagery` branch. The workflow reads
`data/Sabah_Rail_Study_Area_and_Alignment.geojson`, buffers the corridor by
3 km, audits the current imagery source across the alignment and surrounding
context, generates the static 8K images, requests the Level-19 offline tile
package, writes metadata, and uploads the complete result as a workflow
artifact.

## Licence boundary

The authenticated export service returns a TPK/TPKX for offline use. It is not
a direct GeoTIFF export service. The workflow preserves the authorized package
and does not unpack, scrape, or convert it into a GeoTIFF. The static PNG carries
the required Esri/Vantor/Earthstar/GIS User Community attribution.
