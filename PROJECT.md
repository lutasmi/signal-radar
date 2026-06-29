Signal Radar - Project

## Vision

Signal Radar detects early market-relevant signals by combining public political trades, insider purchases, and federal contract awards in a simple, auditable workflow.

## Project purpose

The project collects data from the current approved sources, stores normalized raw records in Google Sheets, rebuilds a deterministic `signals` sheet from those raw tabs, and derives simple intelligence alerts in `cluster_signals`, `correlation_signals`, `priority_signals`, and `review_queue`. The `review_queue` worksheet tracks daily lifecycle changes so a user can see what is new, active, closed, and worth reviewing today. Google Sheets is the source of truth.

## Problem being solved

Useful signals are scattered across independent public sources. Signal Radar keeps those sources in one repeatable pipeline so future phases can correlate events without relying on local files as state.

## Non-goals

Current non-goals are Telegram alerts, complex scoring, dashboards, backtesting, new external sources, databases, Docker, Redis, microservices, and browser automation such as Playwright.

## Current architecture

Capitol Trades -> CSV -> `raw_capitol_trades`

SEC Form 4 -> CSV -> `raw_sec_form4`

USASpending -> CSV -> `raw_usaspending`

`raw_capitol_trades`, `raw_sec_form4`, and `raw_usaspending` -> `signals`

`signals` -> `cluster_signals`

`signals` and `cluster_signals` -> `correlation_signals`

`cluster_signals` and `correlation_signals` -> `priority_signals`

`priority_signals` -> `review_queue`

CSV files are generated artifacts. They are not the source of project state.

## Internal architecture

* `collectors/` fetch approved external sources and write CSV artifacts.
* `loaders/` append CSV rows idempotently into raw Google Sheets tabs.
* `radar/` contains reusable engine helpers for Google Sheets access, row handling, date parsing, stable IDs, and loader deduplication.
* `scripts/build_*` modules transform Google Sheets data into derived worksheets.
* `scripts/validate_*` modules verify deterministic generation and worksheet consistency.
* `scripts/rebuild_radar.py` is the single deterministic execution entry point for the complete derived radar.
* `scripts/validate_all.py` is the single local validation command for safe pre-Sheets checks.
* `tests/fixtures/` contains tiny deterministic CSV fixtures so local validation works in clean checkouts.

## Guiding principles

Prefer simple code, few dependencies, readable functions, shared helper modules, deterministic transforms, idempotent loaders, preserved manual review fields, and validations that can run before touching external services.

## Long-term roadmap

1. Add external alert delivery.
2. Add dashboard.
3. Add backtesting.

Later phases must not start until the current pipeline is stable.

## Definition of success

The current baseline succeeds when each approved source can be collected, loaded idempotently into its raw Google Sheets tab, rebuilt into `signals` deterministically, summarized into explainable `cluster_signals`, connected through auditable `correlation_signals`, ordered into simple `priority_signals`, and delivered to an internal `review_queue` that preserves manual notes while tracking `NEW`, `ACTIVE`, and `CLOSED` opportunities. Later product phases should preserve that baseline.
