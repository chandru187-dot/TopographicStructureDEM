# Sabah Rail integrated model · V0.2

Working Python/FastAPI model with immutable run snapshots, evidence alternatives, modular demand mathematics, freight allocation, operations, road/safety screening, financial calculations and a browser dashboard. Status: **DRAFT_PARTIAL**. This is a progressive V1 runnable milestone, not an approved final forecast or a live Google Cloud service.

Private project evidence is intentionally excluded from the public GitHub branch. The controlled private release contains data, extraction scripts, audit workbook and governance registers. Source originals are preserved in their existing project locations.

## Cloud build commands

```bash
pip install .[test,ingest]
python -m unittest discover -s tests -v
python scripts/run_model.py --scenario scenario.json --output run.json
```

Set `MODEL_DATA_PATH` to the controlled dataset and `DATABASE_URL` to a private PostgreSQL endpoint. Production requires `MODEL_API_TOKEN`; API calls fail closed without it. Start `uvicorn railmodel.api:app --host 0.0.0.0 --port 8080` in the cloud container. Development SQLite is only a test fallback and is unsuitable for Cloud Run persistence.

The SQL migration requires PostGIS. Terraform provides the Cloud Run runtime, scale-to-zero settings, a read-only private evidence volume and selected Secret Manager access. A persistent database, identity gateway, backup policy and XLSX worker require authenticated cloud setup; none is claimed to have been deployed.

## Module implementation status

| Module | V0.2 result |
|---|---|
| M00–M02 Governance / ingestion / geography | Hashed sources, classified alternatives, private dataset and schema; station/zone baseline unresolved |
| M03–M06 Passenger | Source forecasts reproduced, explicit interpolation, generation/logit/IPF primitives tested; actual calibrated OD/capture not available |
| M07–M08 Freight | Market engine and directional OD reproduced, alternative report forecasts retained; operator sample not expanded |
| M09 Traffic | Directional PCU observations inventoried; diversion/VKT calculated only with explicit inputs; road assignment not calibrated |
| M10 Operations | Frequency/capacity and directional station-load algorithms; actual station OD/train capacities unresolved |
| M11 Safety | Observed accident totals and exposure-based screening; no default accident-benefit rate adopted |
| M12–M13 Economics / finance | Conditional benefits, revenue and DCF primitives; approved cost/time/unit-value inputs unresolved |
| M14–M15 Scenarios / QA | Forecast/scenario/sensitivity runs and automated reconciliation tests |
| M16 Reporting | JSON/CSV exports; private cloud Work artifact-tool Excel worker, 20-sheet audit workbook |
| M17–M18 API / dashboard | Authenticated API and same-origin browser UI; persistent deployment pending |

Read `docs/ARCHITECTURE.md`, `docs/USER_GUIDE.md` and the private methodology/QA reports before interpreting a run. Gemini review is prepared but has not been performed.
