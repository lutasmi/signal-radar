# AGENTS.md

## Project

This repository contains **Signal Radar**, a personal research system to detect potentially relevant political and insider trading signals in US equities.

The system combines:

* Congressional PTR trades
* SEC Form 4 insider purchases
* USASpending federal contracts
* Google Sheets as visible historical storage
* Telegram as alert channel

## Current Priority

The current priority is **data acquisition and validation**, not scoring or alerting.

Active task sequence:

1. Capitol Trades → extract usable PTR data
2. Capitol Trades → generate CSV with enough records
3. Capitol Trades → Google Sheets
4. SEC Form 4 → Google Sheets
5. USASpending → Google Sheets
6. Only after that: scoring and Telegram alerts

## Hard Rules

* Work on one executable deliverable at a time.
* Do not design future components until the current component works.
* Do not add new sources without explicit approval.
* Do not build dashboards, APIs, Docker, PostgreSQL, microservices or cloud infrastructure.
* Do not touch secrets.
* Do not commit `.env`, `credentials.json`, tokens or API keys.
* Do not modify `main` directly.
* Use branches and pull requests.
* Prefer simple Python scripts over abstractions.
* Prioritize real data inspection over architecture.

## Approved Sources

Approved for V1:

* Capitol Trades for Congressional PTR
* SEC EDGAR Form 4 for insiders
* USASpending for federal contracts
* Google Sheets for historical storage
* Telegram for alerts

Rejected or deferred for V1:

* Senate eFTS
* Quiver
* Yahoo Finance / yfinance
* Twitter/X
* Playwright unless explicitly approved
* Options flow
* Real-time ingestion

## Current Known State

Validated:

* Capitol Trades first page is parseable.
* Capitol Trades contains real PTR rows.
* Capitol Trades has browser pagination, but simple curl pagination is blocked by Vercel/RSC behavior.
* SEC Form 4 is accessible.
* USASpending is accessible.
* Telegram bot works.
* Google Sheets API works.

Current open issue:

* Find a reliable way to extract multiple pages or enough recent records from Capitol Trades without overengineering.

## Agent Workflow

Before modifying code, always:

1. Explain the plan.
2. List files to be changed.
3. Implement only the requested task.
4. Run the relevant command or test.
5. Summarize the result.
6. State whether the task is solved or blocked.

## Output Expectations

Every task must end with one of:

* `DONE`: working implementation with evidence.
* `BLOCKED`: clear reason, attempted approaches, and next recommended step.
* `NEEDS_DECISION`: requires user approval before continuing.

Do not continue beyond the assigned task.


## Flujo de trabajo Git

En este proyecto se trabaja directamente sobre la rama `main`.

No crear ramas.
No crear Pull Requests.
No hacer commits.
No hacer push.

Modificar únicamente los archivos necesarios y dejar los cambios preparados para que el usuario los revise y decida cuándo hacer commit.