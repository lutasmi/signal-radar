import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

from radar.records import get_value
from radar.sheets import open_sheet, replace_worksheet
from scripts import build_signals_sheet


SOURCE_KEY_COLUMNS = {
    build_signals_sheet.RAW_CAPITOL_TRADES: [
        "politician",
        "transaction_date",
        "asset_name",
        "trade_type",
        "amount",
    ],
    build_signals_sheet.RAW_SEC_FORM4: [
        "source_url",
        "ticker",
        "insider_name",
        "transaction_date",
        "transaction_code",
        "acquired_disposed",
        "shares",
        "price",
    ],
    build_signals_sheet.RAW_USASPENDING: ["award_id"],
}


def fingerprint(row, header, mode, worksheet_name):
    if mode == "exact":
        columns = header
    elif mode == "source-key":
        columns = SOURCE_KEY_COLUMNS[worksheet_name]
    else:
        raise ValueError(f"Unknown dedupe mode: {mode}")
    return tuple(get_value(row, column) for column in columns)


def dedupe_rows(rows, header, mode, worksheet_name):
    seen = set()
    kept = []
    removed = []

    for row in rows:
        key = fingerprint(row, header, mode, worksheet_name)
        if key in seen:
            removed.append(row)
            continue
        seen.add(key)
        kept.append(row)

    return kept, removed


def dedupe_raw_sheets(mode, apply_changes):
    sheet = open_sheet()
    total_removed = 0

    for worksheet_name, header in build_signals_sheet.RAW_HEADERS.items():
        rows = build_signals_sheet.read_records(sheet, worksheet_name)
        kept, removed = dedupe_rows(rows, header, mode, worksheet_name)
        total_removed += len(removed)

        print(f"\n== {worksheet_name} ==")
        print(f"Rows before: {len(rows)}")
        print(f"Rows after: {len(kept)}")
        print(f"Rows removed: {len(removed)}")

        if apply_changes and removed:
            replace_worksheet(sheet, worksheet_name, header, kept)
            print("Worksheet rewritten")

    if not apply_changes:
        print("\nDry-run complete: no Google Sheets data changed.")
    else:
        print(f"\nRaw sheet dedupe complete. Rows removed: {total_removed}")

    return total_removed


def parse_args():
    parser = argparse.ArgumentParser(
        description="Remove duplicate rows from raw Google Sheets worksheets."
    )
    parser.add_argument(
        "--mode",
        choices=["exact", "source-key"],
        default="exact",
        help="exact removes identical rows; source-key uses loader uniqueness keys.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite raw worksheets. Without this flag, the command is read-only.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    dedupe_raw_sheets(mode=args.mode, apply_changes=args.apply)


if __name__ == "__main__":
    main()
