# Approved execution architecture change

The user's current instruction makes GitHub Actions the primary compute environment. Earlier Google Cloud runtime/database/export-worker proposals are **Superseded for primary execution**. Numerical passenger/freight alternatives remain Draft; the architecture change does not approve them.

## Platform responsibilities

Work orchestrates project-only evidence selection, code development, dispatch, diagnostics, decryption, review and Drive delivery. GitHub stores generic code, tests, workflow definitions and encrypted request blobs. Standard GitHub Actions Ubuntu jobs execute Python and produce audit outputs. Drive stores original inputs and new versioned finished outputs. User devices are only needed for instructions, review and the unavoidable one-time secret setup.

The engine is unchanged except its release version. Batch scenarios call the same Engine as the existing API. Outputs include exact source snapshot, data SHA256, model/Git versions, UUIDs, effective assumptions, validation, warnings, source/OD/QA/scenario CSVs, SQLite run register, HTML report and Python-generated XLSX. Excel formula mirrors have Python-calculated caches; Excel is not a competing forecast engine.

## Data persistence

JSON evidence snapshots and append-only run packages are persisted in controlled Drive. SQLite travels within each output bundle and is an audit artifact, not a concurrent shared database. The existing PostgreSQL/PostGIS schema is retained and exercised in a temporary Actions service container. The service stops when the job ends; its results/tests are checked during the job. This deliberately avoids an always-on paid database. No persistent multi-user database or always-on API is claimed.

## Private bridge

1. Work obtains an authorised, pinned project evidence snapshot through the connected Drive/Library tools. File recency is not approval.
2. Work encrypts the snapshot with a dedicated Fernet key (AES128-CBC plus authenticated HMAC). The key is stored separately in private controlled storage and GitHub Actions Secrets. It is never committed or printed.
3. Only `requests/<UUID>/input.enc` and an immutable public metadata/checksum manifest enter the repository. Sensitive forecast values/survey records never enter plaintext commits or caches.
4. A manually selected private workflow checks the request path/schema/hash, decrypts into runner temporary storage, executes the selected draft or approved policy, writes outputs, encrypts the output ZIP and uploads only the encrypted artifact.
5. Work retrieves the artifact, verifies authenticated decryption and manifest hashes, reviews QA, and saves new versioned outputs to private Sabah Rail Drive. Original files are not modified.

The existing ChatGPT Drive connection is not a transferable GitHub runner credential. This Work bridge therefore needs no new Drive OAuth client or paid cloud account. A future direct Drive API adapter would need separate explicit authentication; it is not required for this first working architecture.

Public workflows use only synthetic fixture values. Public logs contain test outcomes, elapsed runtime and opaque identifiers, not private results. No `pull_request_target` or fork-triggered secret job is used. Private job timeout is 30 minutes, bridge expansion/extraction cap 50 MiB, output archive paths validated, secret absence/tampering rejected. Dependency caching is separate from data. Secret rotation requires re-encrypting requests; existing ciphertext cannot be decoded with a new key.

## Workflow placement

Model code remains on `sabah-rail-actions-v0.3.0`; the new dispatch workflow is also added to the default branch so GitHub displays Run workflow. Only that new workflow is added to the default branch. Checkout is fixed to the model branch and records its exact commit. No mapping or imagery file is replaced.

## Cost and limits

Use standard public-repository Ubuntu runners and short-retention small artifacts first. Public code availability does not imply public project data. Included account usage/storage/billing settings must be respected; this release makes no promise of unlimited artifact storage or paid overage. No large runner, self-hosted runner or external compute service is configured.

A compute limitation register must identify workload, dataset size, job timeout/resource measurements, split/cache/stream alternatives and retest. No measured limitation of the tested model/export workloads currently justifies external compute. Full-network assignment, very large rasters and persistent interactive services have not been benchmarked in this release.

Official references checked: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax ; https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow ; https://github.com/postgis/docker-postgis
