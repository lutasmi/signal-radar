"""Print a compact health report of the congress tracker worksheets (read-only).

Used in CI logs to verify what is really stored in Google Sheets: header,
row counts, column fill rates and a sample row per worksheet.
"""

import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

WORKSHEETS = ["raw_congress_ptr", "politician_scorecard", "replica_holdings", "conflict_trades", "agent_usage"]


def report(sheet, name):
    import gspread

    try:
        values = sheet.worksheet(name).get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        print(f"== {name}: missing")
        return
    print(f"== {name}: {len(values)} rows incl. header")
    if not values:
        return
    header = values[0]
    print(f"header ({len(header)}): {header}")
    widths = Counter(len(row) for row in values[1:])
    print(f"row widths: {dict(widths.most_common(5))}")
    filled = {
        column: sum(1 for row in values[1:] if index < len(row) and row[index].strip())
        for index, column in enumerate(header)
    }
    print(f"non-empty per column: {filled}")
    for row in values[1:3]:
        print(f"sample: {dict(zip(header, row))}")


def main():
    from dotenv import load_dotenv

    from radar.sheets import open_sheet

    load_dotenv()
    sheet = open_sheet()
    for name in WORKSHEETS:
        report(sheet, name)


if __name__ == "__main__":
    main()
