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
review_queue -> telegram_alert_log -> Telegram
```

Local CSV files in `data/` are generated artifacts. They are useful for repeatable loads and offline validation, but they are not project state.

## Current Phase

The current baseline includes ingestion -> raw sheets -> signals plus intelligence layers in `cluster_signals`, `correlation_signals`, `priority_signals`, and `review_queue`. The radar identifies repeated ticker activity, repeated contract activity, simple explainable correlations, a small auditable priority list, and a daily review queue.

## Main Commands

Use Python 3.11, which is the runtime used by CI and GitHub Actions. The main local entrypoints can re-exec through the project `.venv` when `python3` points to a different interpreter without the required dependencies.

Collectors write local CSV artifacts:

```bash
python3 collectors/collect_capitol_trades.py
python3 collectors/collect_sec_form4.py
python3 collectors/collect_usaspending.py
```

Candidate-source collectors write auditable raw responses under `data/raw/<source>/`
and normalized CSVs under `data/processed/<source>/`. They are intentionally not
loaded into Google Sheets yet:

```bash
python3 collectors/collect_sam_gov.py
python3 collectors/collect_congress_gov.py --use-demo-key
python3 collectors/collect_federal_register.py
python3 collectors/collect_lda_gov.py --filing-year 2026
python3 collectors/collect_sec_edgar_additional.py
python3 collectors/collect_fec.py --use-demo-key --period 2026
python3 collectors/collect_grants_gov.py --keyword energy
python3 collectors/collect_uspto.py
```

SAM.gov and USPTO require real API keys for live extraction. Congress.gov and
OpenFEC support `DEMO_KEY` for small validation samples, but production use
should set `CONGRESS_GOV_API_KEY` and `FEC_API_KEY`.

The USASpending collector combines recent Department of Defense awards with a
second pass over the largest recent awards, deduplicated by `award_id`.

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

Validate live raw Google Sheets source quality without writing:

```bash
python3 scripts/validate_raw_quality.py
python3 scripts/collect_daily_metrics.py
```

Remove exact duplicate rows from raw Google Sheets tabs only after reviewing the
dry-run output:

```bash
python3 scripts/dedupe_raw_sheets.py
python3 scripts/dedupe_raw_sheets.py --apply
```

GitHub Actions runs the same fixture-backed local validation on every push and pull request through `.github/workflows/local_validation.yml`. The workflow does not run collectors, loaders, Google Sheets writes, or credential-dependent checks.

Daily automation is defined in `.github/workflows/daily_radar.yml`. It can be started manually from GitHub Actions using the `Daily radar` workflow, and it also runs once per day on schedule. Required repository secrets:

* `GOOGLE_CREDENTIALS_JSON`: full Google service account JSON.
* `GOOGLE_SHEETS_ID`: target spreadsheet ID.
* `TELEGRAM_BOT_TOKEN`: Telegram bot token.
* `TELEGRAM_CHAT_ID`: destination chat ID.

The daily workflow writes Google secrets into runner-local `credentials.json` and `.env`, runs the three approved collectors, loads CSV artifacts into raw Google Sheets tabs, runs `python3 scripts/rebuild_radar.py`, then sends Telegram alerts. Generated CSVs remain ignored by git and are not committed.

The scheduled run executes at `07:15 UTC` every day. During daylight saving time
in Madrid, that is `09:15 Europe/Madrid`.

After every source collection step, the workflow sends a Telegram capture summary
with the sources consulted, extracted CSV row counts, and any collector failure.
The same summary can be checked locally without sending a message:

```bash
python3 scripts/send_capture_summary.py --dry-run
```

External sources can fail transiently. Capitol Trades can return HTTP 429 rate limits from GitHub Actions, and the SEC or USASpending endpoints can be unavailable or slow. When a collector fails in the daily workflow, the workflow logs a warning, skips only that source's loader for the run, and rebuilds the derived radar from the raw Google Sheets data already available.

The `review_queue` worksheet is the morning change tracker. It keeps `review_status` and `review_note` as manual fields, and adds lifecycle fields:

* `status`: `NEW`, `ACTIVE`, or `CLOSED`.
* `review_today`: `YES` for new or high-priority active rows.
* `first_seen`: first date the opportunity appeared in the queue.
* `last_seen`: latest pipeline date the opportunity was still present.
* `closed_date`: date an opportunity disappeared from current priorities.
* `score`: deterministic configurable score.
* `score_band`: alert band such as `HIGH`, `MEDIUM`, or `LOW`.
* `score_reason`: auditable explanation of score components.

Scoring weights live in `config/scoring.json`, so weights can change without editing Python. The default config covers priority level, source diversity, cross-source/correlation types, signal types, repeated activity, recency, `review_today`, and lifecycle status.

Telegram alerts are generated from `review_queue`:

```bash
python3 scripts/send_telegram_alerts.py --test
python3 scripts/send_telegram_alerts.py --dry-run
python3 scripts/send_telegram_alerts.py
```

`--test` sends a fixed Telegram integration test message without reading or writing Google Sheets.

Default alert rule: `status = NEW`, `review_today = YES`, or `score_band = HIGH`. Sent alerts are appended to `telegram_alert_log`; reruns skip alerts whose `alert_id` is already logged. Dry-run prints candidate messages without sending Telegram messages or writing alert history.

Morning workflow: check Telegram for concise high-priority radar alerts, then open Google Sheets for deeper review and manual notes.

## Internal Architecture

* `collectors/`: source-specific CSV collectors.
* `loaders/`: idempotent CSV -> raw Google Sheets loaders.
* `radar/`: shared engine helpers for Sheets, rows, dates, stable IDs, and loader deduplication.
* `scripts/build_*`: deterministic transformations for derived worksheets.
* `scripts/validate_*`: local and Google Sheets validation.
* `scripts/rebuild_radar.py`: single entry point for the complete derived radar and daily review lifecycle.
* `scripts/send_telegram_alerts.py`: sends deduplicated Telegram alerts from `review_queue`.
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

Telegram operations require `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` locally or as GitHub repository secrets.

SEC collection should set `SEC_USER_AGENT` in `.env` locally and as a GitHub
repository secret for daily runs.

## Restrictions

Do not add databases, Docker, Redis, microservices, Playwright, new data sources, new alert channels, or complex scoring unless explicitly requested. Telegram is the approved V1 alert channel. Future phases are tracked in `ROADMAP.md`.
