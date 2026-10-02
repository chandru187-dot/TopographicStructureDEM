# Use the model

This is a functioning V0.2 draft build, tested in the cloud Work workspace. It is not yet an authenticated public/persistent Google Cloud deployment. No personal computer needs to remain switched on once deployed.

## Browser workflow

Open the deployed/private dashboard, enter your API token for the session, choose forecast year, passenger scenario, alignment option, freight evidence and freight package, then Run model. Review warnings before interpreting results. Select Freight OD for directional tonnes/TEU; Passenger OD evidence for sample respondent pairs; Operations for frequency/capacity; Audit for equations/checks; Sources/issues for provenance and decisions. Download run JSON or freight OD CSV. Compare 10/20/30 minutes creates three saved runs.

Default Base/2033/Option 3/S2 values are merely a draft screen selection. They do not establish the project baseline. A source High-only alternative must use High and Option 3. Headway changes source demand only if a labelled user-supplied sensitivity is requested; no calibrated response is assumed.

## Run assumptions and units

| Name | Unit / treatment |
|---|---|
| passenger_days / freight_days | operating days/year; different conventions |
| private_diversion_share | share of source rail passengers diverted from private vehicles |
| occupancy | persons/private vehicle |
| passenger_distance_km / freight_distance_km | comparable road trip/haul km |
| passenger_access_vkt / freight_feeder_vkt | added road vehicle-km/year; enter zero explicitly only for a stated sensitivity |
| truck_payload_t | tonnes per diverted road truck trip |
| freight_road_diversion_share | road-origin share of eligible freight; existing rail already excluded |
| headway_min / reference_headway_min | minutes |
| headway_elasticity | supplied demand elasticity, -2..0; absent means no calibrated demand change |
| train_capacity | passengers per train, usable capacity |
| service_hours | scheduled operating hours/day |
| effective_teu_per_train | net effective TEU/train including loading/utilisation treatment |
| net_tonnes_per_freight_train | tonnes/non-container train |
| rail_invehicle_min / access_min / egress_min / transfer_min / road_door_min | minutes; waiting is headway/2 |
| baseline_annual_vkt / future_annual_vkt | comparable road exposure vehicle-km/year |
| value_time_rm_hour | RM/person-hour, unapproved unless separately established |
| car_voc_rm_km / truck_voc_rm_km | RM/vehicle-km |
| net_freight_saving_rm_tkm | RM/tonne-km excluding separately counted truck VOC |
| crash_cost_rm | RM/accident; severity mix must be justified |
| fare_rm_trip / freight_tariff_rm_t | financial fares/tariffs; not economic benefits |
| annual_rail_operating_cost_rm | RM/year, financial cost |
| freight_capture_multiplier | sensitivity multiplier; effective capture capped at 100% |

Missing inputs remain null/Unresolved. Do not infer missing quantities from a zero subtotal. A full station matrix must carry its ordered station IDs, source and status and reconcile with the selected passenger total. Source station catchment trip ends are not a station OD matrix.

## Builder commands (cloud environment)

Install `pip install .[test,ingest]` in a cloud job. Run `python -m unittest discover -s tests -v`. Start a private test service with `MODEL_DEV_MODE=1 python -m uvicorn railmodel.api:app --host 127.0.0.1 --port 8080`. Production uses `MODEL_API_TOKEN` and a TLS PostgreSQL `DATABASE_URL`, not development mode or an ephemeral SQLite file.

`scripts/run_model.py --scenario <JSON> --output <JSON>` calculates and persists a run. `scripts/import_postgres.py` loads a dataset without replacing existing versions; pin `MODEL_DATABASE_DATA_VERSION` to read it. The Work XLSX worker consumes `outputs/excel_bundle.json` through `scripts/export_excel.mjs`. Do not install/render that workbook with an unverified replacement library.

## Google Cloud activation gate

Authenticate the chosen Google Cloud account/project, confirm billing/cost limits, authorise required IAM, create/choose private PostgreSQL/PostGIS with backup/retention, populate Secret Manager and private evidence bucket, then run reviewed Terraform/container deployment. Browser access needs an authenticated user gateway/IAP. The database and XLSX renderer worker are not yet provisioned. The first cloud deployment should run the same evidence regression suite before becoming an approved service.
