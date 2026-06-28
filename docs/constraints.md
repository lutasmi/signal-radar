# Signal Radar Constraints

These constraints apply to the current project phase.

## Source of Truth

Google Sheets is the source of truth.

Valid raw tabs:

* `raw_capitol_trades`
* `raw_sec_form4`
* `raw_usaspending`

Derived tab:

* `signals`
* `cluster_signals`
* `correlation_signals`
* `priority_signals`
* `review_queue`

Local CSV files are generated artifacts only.

## Current Architecture

Capitol Trades -> CSV -> `raw_capitol_trades`

SEC Form 4 -> CSV -> `raw_sec_form4`

USASpending -> CSV -> `raw_usaspending`

Raw Google Sheets tabs -> `signals`

`signals` -> `cluster_signals`

`signals` and `cluster_signals` -> `correlation_signals`

`cluster_signals` and `correlation_signals` -> `priority_signals`

`priority_signals` -> `review_queue`

## Internal Layout

* `collectors/` fetch approved source data.
* `loaders/` load CSV artifacts into raw Google Sheets tabs.
* `radar/` contains reusable internal helpers.
* `scripts/build_*` contains deterministic transformations.
* `scripts/validate_*` contains validation entry points.
* `scripts/rebuild_radar.py` runs the complete derived radar.

## Restrictions

Do not introduce:

* SQLite
* PostgreSQL
* Docker
* Redis
* microservices
* Playwright
* Telegram
* complex scoring
* new external sources

Do not redesign the architecture without an explicit product decision.

## Workflow

Work directly on `main`. Do not create branches, commits, pull requests, or pushes unless explicitly requested.

Before adding new functionality, keep the repository internally coherent, loaders idempotent, signal generation deterministic, cluster alerts explainable, correlations auditable, priorities simple, and review status preserved.
