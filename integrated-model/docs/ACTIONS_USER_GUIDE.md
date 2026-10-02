# Use the Actions model

Give Work an instruction such as “Run the 2045 Sabah Rail Base scenario and regenerate the workbook.” Work selects the controlled data version, preserves draft/alternative status, prepares an encrypted request, commits ciphertext only, dispatches the private profile, reads job logs/artifacts, decrypts/validates outputs and saves them in a new Drive run folder. This requires one configured bridge secret. Work is the operator; direct automatic invocation by every ChatGPT session is not assumed where a connector lacks dispatch support. The signed-in GitHub browser can dispatch the workflow.

## One-time private bridge setup

The repository is public. A private-data model run stays locked until encryption is configured. Use a new dedicated Fernet key; never reuse an account password or API token. Work supplies the generated key as a private downloadable file, not in code or logs.

1. Open repository → Settings → Secrets and variables → Actions.
2. Select New repository secret.
3. Name it `SABAH_RAIL_BRIDGE_KEY`.
4. Paste the single line from the private key file and select Add secret.
5. Keep the private key file in controlled project storage so Work can decrypt later run artifacts. Do not send its contents in chat or commit it to GitHub.
6. Tell Work the secret is configured. Work can then prepare/execute the real-data request and return outputs through the existing Drive connection.

No Google Cloud/Oracle registration, payment card, cloud VM or separate Drive OAuth credentials are needed for the Work-mediated bridge.

## Manual workflow controls

Actions → Sabah Rail Actions Model → Run workflow. Select `synthetic` for a fictional execution demonstration, year/case/headway as needed. It generates an explicitly synthetic audit package. Select `private` only with an existing encrypted request path. Year/case/headway controls govern synthetic runs; the private run uses its immutable request manifest scenario to prevent silently changing the audited request.

A 10/20/30-minute comparison runs the same demand source schedule unless an explicit uncalibrated elasticity is supplied. Passenger sample OD is unweighted respondent evidence. Freight allocation OD is a draft market pattern. Station boarding/alighting, road assignment, net VKT and exposure-based safety remain conditional on their inputs.

Artifacts expire after three days; Work must save validated results to Drive promptly. Failed validation keeps encrypted diagnostics available, and the workflow is failed rather than labelling it successful. Missing secret/input fails before private model processing.

## Boundaries

This architecture runs jobs on demand; it does not host a permanent FastAPI endpoint. The browser deliverable is a downloadable run report. Persistent interactive hosting is a separate capability that has not been requested as paid infrastructure here. PostgreSQL/PostGIS is tested as a disposable job service; durable evidence/run snapshots remain in Drive.
