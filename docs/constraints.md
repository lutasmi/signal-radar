Repository: signal-radar

Read first:

* AGENTS.md
* Estado_v0.md
* docs/constraints.md if present

Task:
Investigate how to extract multiple pages from Capitol Trades.

Context:
The current script `collectors/collect_capitol_trades.py` successfully parses the first page of https://www.capitoltrades.com/trades and writes `data/capitol_trades_latest.csv`.

Problem:
The first page only gives 12 records. The browser shows 2,979 pages and requests like:
`/trades?page=13&_rsc=...`
but direct curl returns a Vercel Security Checkpoint.

Goal:
Find a reliable low-complexity way to obtain more than the first 12 Capitol Trades records.

Deliverable:
Either:

1. Update or create a script that extracts at least 100 PTR rows from Capitol Trades and writes them to CSV, or
2. Document clearly why this is blocked and what was tried.

Hard constraints:

* Do not touch Google Sheets code.
* Do not touch Telegram code.
* Do not add scoring.
* Do not add SQLite.
* Do not add Playwright unless you first conclude there is no simpler option and explain why.
* Do not add new data sources.
* Do not modify secrets.
* Work in a new branch.
* Do not commit to main.

Expected evidence:

* Command used to run the script.
* Number of rows extracted.
* Example of 5 parsed rows.
* If blocked, exact reason and next recommendation.

End your answer with one of:
DONE / BLOCKED / NEEDS_DECISION
