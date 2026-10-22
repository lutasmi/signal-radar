import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

from radar.records import get_value
from radar.sheets import open_sheet, read_records
from scripts import build_signals_sheet
from scripts.validate_raw_quality import QUALITY_SPECS, duplicate_count, exact_row_key, row_key


MAX_READ_ATTEMPTS = 3
READ_RETRY_SECONDS = [15, 45]

DERIVED_WORKSHEETS = [
    "signals",
    "cluster_signals",
    "correlation_signals",
    "priority_signals",
    "review_queue",
    "conflict_trades",
    "ptr_manual_review",
    "telegram_alert_log",
]


def is_rate_limit_error(error):
    return "429" in str(error) or "Quota exceeded" in str(error)


def read_with_retry(description, read_fn):
    for attempt in range(1, MAX_READ_ATTEMPTS + 1):
        try:
            return read_fn()
        except Exception as error:
            if attempt >= MAX_READ_ATTEMPTS or not is_rate_limit_error(error):
                raise
            wait_seconds = READ_RETRY_SECONDS[attempt - 1]
            print(
                f"WARNING: Google Sheets read quota hit while reading {description}; "
                f"retrying in {wait_seconds}s"
            )
            time.sleep(wait_seconds)


def raw_metrics(sheet):
    lines = ["Raw source metrics", ""]
    for worksheet_name, spec in QUALITY_SPECS.items():
        rows = read_with_retry(
            worksheet_name,
            lambda name=worksheet_name: build_signals_sheet.read_records(sheet, name),
        )
        header = build_signals_sheet.RAW_HEADERS[worksheet_name]
        exact_duplicates = duplicate_count(exact_row_key(row, header) for row in rows)
        logical_duplicates = duplicate_count(
            row_key(row, spec["key_columns"]) for row in rows
        )

        lines.append(f"- {worksheet_name}: {len(rows)} rows")
        lines.append(f"  - exact duplicate rows: {exact_duplicates}")
        lines.append(f"  - logical duplicate rows: {logical_duplicates}")

        if worksheet_name == build_signals_sheet.RAW_CAPITOL_TRADES:
            blank_tickers = sum(1 for row in rows if not get_value(row, "ticker"))
            lines.append(f"  - blank ticker rows: {blank_tickers}")

    return lines


def derived_metrics(sheet):
    lines = ["", "Derived worksheet metrics", ""]
    for worksheet_name in DERIVED_WORKSHEETS:
        try:
            rows = read_with_retry(
                worksheet_name,
                lambda name=worksheet_name: read_records(sheet, name),
            )
        except Exception:
            rows = []
        lines.append(f"- {worksheet_name}: {len(rows)} rows")

        if worksheet_name == "signals" and rows:
            counts = Counter(get_value(row, "signal_type") for row in rows)
            lines.append(f"  - signal_type: {dict(sorted(counts.items()))}")
        if worksheet_name == "review_queue" and rows:
            counts = Counter(get_value(row, "status") for row in rows)
            lines.append(f"  - status: {dict(sorted(counts.items()))}")
            today = sum(1 for row in rows if get_value(row, "review_today") == "YES")
            lines.append(f"  - review_today YES: {today}")

    return lines


def write_github_summary(lines):
    summary_path = Path(str(Path.cwd() / "__no_github_summary__"))
    import os

    github_step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if github_step_summary:
        summary_path = Path(github_step_summary)
        with summary_path.open("a", encoding="utf-8") as f:
            f.write("## Signal Radar metrics\n\n")
            f.write("\n".join(lines))
            f.write("\n")


def collect_daily_metrics():
    sheet = open_sheet()
    lines = raw_metrics(sheet) + derived_metrics(sheet)
    print("\n".join(lines))
    write_github_summary(lines)
    return lines


def main():
    collect_daily_metrics()


if __name__ == "__main__":
    main()
