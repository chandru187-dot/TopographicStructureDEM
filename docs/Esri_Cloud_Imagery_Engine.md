# Cloud Esri World Imagery Engine

This workflow is the cloud replacement for the ArcGIS Pro imagery-download step.

## What it does

- Reads any WGS84 GeoJSON project area/alignment from the repository.
- Checks Esri World Imagery source metadata, including native source resolution where reported.
- Runs entirely on a GitHub-hosted Linux runner.
- When an authorized ArcGIS credential is present, calls the official export-enabled World Imagery service and downloads the returned TPK/TPKX package.
- Automatically splits large areas into smaller export requests when the service tile limit would otherwise be exceeded.
- Does not require ArcGIS Pro, ArcPy, CityEngine, or a Windows PC.

## Request file

Edit `config/esri_cloud_request.json` to point at another project. A commit to that file triggers the workflow automatically.

The initial request uses the Sabah Rail GeoJSON as the first test, but the engine itself is project-independent.

## Authentication

The workflow supports, in order:

1. `ARCGIS_TOKEN` — short-lived user access token; simplest for the current ArcGIS Online trial.
2. `ARCGIS_API_KEY` — if the account/token is authorized for the export service.
3. `ARCGIS_CLIENT_ID` + `ARCGIS_REFRESH_TOKEN` — preferred persistent user-auth flow.
4. `ARCGIS_CLIENT_ID` + `ARCGIS_CLIENT_SECRET` — app authentication for subscriptions that support it.

ArcGIS Online Trial does not support app authentication, so the trial proof-of-concept should use a user access token or user-auth refresh-token flow.

Do not commit credentials to the repository. Store them only in GitHub Actions secrets.

## Output

The official offline export is retained as Esri TPK/TPKX. The workflow intentionally does not scrape individual basemap tiles or convert Esri's licensed World Imagery into an unrestricted GeoTIFF.
