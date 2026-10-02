# Sabah Rail integrated model — GitHub Actions V0.3

Primary execution: **ChatGPT Work → GitHub → GitHub Actions → Python → private Google Drive outputs**. One authoritative engine. The V0.2 calculation modules remain; V0.3 adds batch execution, portable Python XLSX export, scenario comparisons, encrypted handoff and disposable PostgreSQL/PostGIS tests.

No paid compute provider is configured or required for this implementation. Prior cloud-provider starters on the older model branch are retained as historical alternatives and are not used by the Actions workflows.

## Run

Open the repository Actions tab → **Sabah Rail Actions Model** → Run workflow. `synthetic` runs public execution tests with fictional data and generates JSON, CSV, HTML, SQLite audit snapshots and a 20-sheet Excel workbook. `private` requires a Work-prepared encrypted request and the repository secret `SABAH_RAIL_BRIDGE_KEY`. Private results are encrypted before artifact upload; Work decrypts them and returns them to the controlled Sabah Rail Drive folder.

Synthetic results are never presented as Sabah Rail forecasts. An actual numerical baseline has not been approved. Draft alternatives can be executed only through an explicitly labelled draft request; approved-only processing fails closed until baseline approval exists.

```bash
pip install -r requirements-actions.txt
python -m unittest discover -s tests -v
python -m railmodel.batch --synthetic --compare --output outputs/demo
python -m railmodel.batch --data PRIVATE_DATA.json --input-policy explicit_draft --year 2045 --compare --output PRIVATE_OUTPUTS
```

Dependencies come from public Python registries. GitHub-hosted standard Ubuntu runners are used; dependency cache contains no project input/output data. Jobs are bounded to 15/30 minutes; artifacts are retained for three days. Durable project outputs and registers belong in private Drive, not runner storage.

Read `docs/ACTIONS_ARCHITECTURE.md` and `docs/ACTIONS_USER_GUIDE.md`. Existing map/imagery branches and workflows are preserved. This is a functioning batch platform, not an always-on API/dashboard server; the previous API/UI code remains available for Work development. No external compute migration is justified without measured workload evidence and optimisation tests.
