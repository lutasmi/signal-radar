# Signal Radar

Signal Radar combines public Capitol Trades, SEC Form 4 insider purchases, and USASpending federal contract data into a simple Google Sheets pipeline.

Google Sheets is the source of truth:

```text
Capitol Trades -> raw_capitol_trades
SEC Form 4    -> raw_sec_form4
USASpending   -> raw_usaspending

raw_* sheets  -> signals
signals       -> cluster_signals
cluster_signals + signals -> correlation_signals
cluster_signals + correlation_signals -> priority_signals
priority_signals -> review_queue
```

Local CSV files in `data/` are generated artifacts. They are useful for repeatable loads and offline validation, but they are not project state.

## Current Phase

The current baseline includes ingestion -> raw sheets -> signals plus intelligence layers in `cluster_signals`, `correlation_signals`, `priority_signals`, and `review_queue`. The radar identifies repeated ticker activity, repeated contract activity, simple explainable correlations, a small auditable priority list, and a daily review queue.

## Main Commands

Use Python 3.11, which is the runtime used by CI and GitHub Actions.

Collectors write local CSV artifacts:

```bash
python3 collectors/collect_capitol_trades.py
python3 collectors/collect_sec_form4.py
python3 collectors/collect_usaspending.py
```

Loaders append new rows to Google Sheets raw tabs:

```bash
python3 loaders/load_capitol_trades_csv_to_sheets.py
python3 loaders/load_sec_form4_csv_to_sheets.py
python3 loaders/load_usaspending_csv_to_sheets.py
```

Signals are rebuilt from Google Sheets:

```bash
python3 scripts/build_signals_sheet.py
```

The complete derived radar can be rebuilt in order:

```bash
python3 scripts/rebuild_radar.py
```

If a raw source worksheet is missing or a source collection was skipped, the rebuild logs a warning and continues with the raw worksheets that are available.

Run all safe local validations without touching Google Sheets:

```bash
python3 scripts/validate_all.py
```

By default this command uses tracked CSV fixtures in `tests/fixtures/`, so it can run in a clean checkout without generated data, credentials, internet, or Google Sheets access. It compiles Python modules, checks patch whitespace, and validates loader idempotency, deterministic derived layers, missing-source tolerance, and `review_queue` lifecycle preservation. To validate the current ignored CSV artifacts in `data/`, run:

```bash
python3 scripts/validate_all.py --generated-csv
```

GitHub Actions runs the same fixture-backed local validation on every push and pull request through `.github/workflows/local_validation.yml`. The workflow does not run collectors, loaders, Google Sheets writes, or credential-dependent checks.

Daily automation is defined in `.github/workflows/daily_radar.yml`. It can be started manually from GitHub Actions using the `Daily radar` workflow, and it also runs once per day on schedule. Required repository secrets:

* `GOOGLE_CREDENTIALS_JSON`: full Google service account JSON.
* `GOOGLE_SHEETS_ID`: target spreadsheet ID.

The daily workflow writes those secrets into runner-local `credentials.json` and `.env`, runs the three approved collectors, loads CSV artifacts into raw Google Sheets tabs, then runs `python3 scripts/rebuild_radar.py`. Generated CSVs remain ignored by git and are not committed.

External sources can fail transiently. Capitol Trades can return HTTP 429 rate limits from GitHub Actions, and the SEC or USASpending endpoints can be unavailable or slow. When a collector fails in the daily workflow, the workflow logs a warning, skips only that source's loader for the run, and rebuilds the derived radar from the raw Google Sheets data already available.

The `review_queue` worksheet is the morning change tracker. It keeps `review_status` and `review_note` as manual fields, and adds lifecycle fields:

* `status`: `NEW`, `ACTIVE`, or `CLOSED`.
* `review_today`: `YES` for new or high-priority active rows.
* `first_seen`: first date the opportunity appeared in the queue.
* `last_seen`: latest pipeline date the opportunity was still present.
* `closed_date`: date an opportunity disappeared from current priorities.

## Internal Architecture

* `collectors/`: source-specific CSV collectors.
* `loaders/`: idempotent CSV -> raw Google Sheets loaders.
* `radar/`: shared engine helpers for Sheets, rows, dates, stable IDs, and loader deduplication.
* `scripts/build_*`: deterministic transformations for derived worksheets.
* `scripts/validate_*`: local and Google Sheets validation.
* `scripts/rebuild_radar.py`: single entry point for the complete derived radar and daily review lifecycle.
* `scripts/validate_all.py`: single local validation command suitable for CI.
* `tests/fixtures/`: tiny deterministic CSV fixtures for local-only validation.

Cluster alerts are rebuilt from the `signals` worksheet:

```bash
python3 scripts/build_cluster_signals_sheet.py
```

Offline validation checks CSV schemas, loader idempotency, and deterministic signal generation without touching Google Sheets:

```bash
python3 scripts/validate_pipeline.py --require-csv
```

Cluster validation checks deterministic local generation and can verify the Google Sheets worksheet:

```bash
python3 scripts/validate_cluster_signals.py --require-csv
python3 scripts/validate_cluster_signals.py --google-sheets
```

Correlation validation checks deterministic local generation and can verify the Google Sheets worksheet:

```bash
python3 scripts/validate_correlation_signals.py --require-csv
python3 scripts/validate_correlation_signals.py --google-sheets
```

Priority validation checks deterministic local generation and can verify the Google Sheets worksheet:

```bash
python3 scripts/validate_priority_signals.py --require-csv
python3 scripts/validate_priority_signals.py --google-sheets
```

Review queue validation checks deterministic local generation and can verify the Google Sheets worksheet:

```bash
python3 scripts/validate_review_queue.py --require-csv
python3 scripts/validate_review_queue.py --google-sheets
```

## Configuration

Google Sheets operations require:

* `GOOGLE_SHEETS_ID` in `.env`
* `credentials.json` for a Google service account

In GitHub Actions, set `GOOGLE_SHEETS_ID` and `GOOGLE_CREDENTIALS_JSON` as repository secrets instead of committing these files.

SEC collection can optionally set `SEC_USER_AGENT` in the environment.

## Restrictions

Do not add databases, Docker, Redis, microservices, Telegram, Playwright, new data sources, or complex scoring unless explicitly requested. Future phases are tracked in `ROADMAP.md`.
