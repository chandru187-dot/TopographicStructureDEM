# V0 architecture and governance

One production calculation engine: Python. Drive retains original documents and new versioned outputs. An evidence ingestion stage hashes documents and extracts structured, locatable input records. PostgreSQL/PostGIS stores versioned datasets, metadata, OD, counts, geography, assumptions and immutable runs. The engine can read a pinned database dataset or a pinned JSON evidence snapshot. FastAPI and the same-origin dashboard call that engine. Excel is a generated audit mirror, not an independent production forecast.

Cloud runtime: Cloud Run, minimum instances zero, maximum two. Private GCS data mount; secrets through Secret Manager. Use PostgreSQL with TLS, backups and a verified PostGIS extension. The Terraform starter does not create a paid database or select an unapproved project. Cloud SQL is the native Google option; it should not be described as automatically scaling to zero. A lower idle-cost managed PostgreSQL service may be considered after permissions, region and retention review.

No physical user server or continuously running personal computer is required. This Work workspace is the build/test environment; it is not a deployed persistent service. No GCP service, PostGIS instance, billing account or Gemini connection was authenticated or deployed in this milestone.

## Interfaces

- `POST /api/runs`: calculate and persist a draft run.
- `GET /api/runs/{id}`: retrieve original results.
- `GET /api/runs/{id}/export.json`: include the complete immutable input snapshot.
- `GET /api/runs/{id}/freight.csv`: directional market OD.
- `POST /api/sensitivity`: three saved headway runs.
- `GET /api/evidence`: source register, unresolved items, counts and sample OD.
- Work export worker: `scripts/export_excel.mjs` consumes a Python export bundle using the supported artifact runtime.
- Future assignment adapter: provide zone/network identifiers, directed OD and period/unit metadata; return link flows/skims plus calibration measures. EMME/VISUM/etc. can integrate through these records without replacing the core engine.

## Versioning and approvals

Code uses semver; this release is `0.2.0` and remains a draft milestone. Data version is SHA-256 of the exact evidence JSON. Every run has a UUID, UTC timestamp, code version/Git commit field, input snapshot, assumptions, scenario, output lineage, checks and warnings. Original documents are never overwritten. Run insertion is append-only at application level; production DB roles must deny UPDATE/DELETE on controlled records.

Approval is separate from recency. Source filenames containing “final”, workbook flags saying “adopted”, and newly generated outputs do not establish JKNS approval. The unresolved register preserves conflicting alternatives. Only architectural scope in the current user request is approved for implementation; passenger/freight baselines are still unresolved.

## Security and access

API requires a bearer token unless an explicit local development mode is enabled. Tokens are never persisted in the browser. GCP IAM remains private; an authenticated browser gateway/IAP is still needed for convenient phone access. Never put project evidence, response-level surveys, credentials or outputs into a public GitHub repository. Public source publication, if used, contains only generic engine/UI/schema/tests/documentation. Protect main branches and require tests/review before a production release.

## Excel runtime boundary

This release includes an automatic, tested audit workbook builder for ChatGPT Work's artifact runtime. GCP container execution of that renderer has not been configured or verified. The API exports JSON/CSV now; unattended XLSX jobs in Google Cloud remain an integration task, rather than a claimed deployed capability. Do not substitute a second forecasting engine in Excel.

Official implementation references checked during the build:
- https://docs.cloud.google.com/run/docs/about-instance-autoscaling
- https://docs.cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts
- https://docs.cloud.google.com/sql/docs/postgres/extensions
- https://docs.cloud.google.com/run/docs/configuring/services/secrets
- https://fastapi.tiangolo.com/deployment/docker/
