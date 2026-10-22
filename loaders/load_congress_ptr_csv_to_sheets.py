import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

from radar import loaders
from radar.sheets import open_or_create_worksheet, open_sheet

CSV_FILE = Path("data/congress_ptr_latest.csv")
WORKSHEET_NAME = "raw_congress_ptr"
UNIQUE_KEY_COLUMNS = [
    "filing_id",
    "owner",
    "asset_name",
    "trade_type",
    "transaction_date",
    "amount",
]


def build_key(header, row):
    return loaders.build_key(header, row, UNIQUE_KEY_COLUMNS)


def build_existing_keys(values, csv_header):
    return loaders.build_existing_keys(values, csv_header, UNIQUE_KEY_COLUMNS)


def filter_new_rows(header, data_rows, existing_keys):
    return loaders.filter_new_rows(header, data_rows, existing_keys, UNIQUE_KEY_COLUMNS)


def append_csv_to_sheet(csv_file: Path):
    header, data_rows = loaders.read_csv(csv_file)
    sheet = open_sheet()
    worksheet = open_or_create_worksheet(
        sheet, WORKSHEET_NAME, row_count=max(len(data_rows), 1000), column_count=len(header)
    )
    existing_values = worksheet.get_all_values()
    existing_keys = build_existing_keys(existing_values, header)
    new_rows = filter_new_rows(header, data_rows, existing_keys)

    header_written = False
    if not existing_values:
        worksheet.append_row(header, value_input_option="RAW")
        header_written = True

    if new_rows:
        # Oldest first so the sheet reads chronologically when appended daily.
        worksheet.append_rows(list(reversed(new_rows)), value_input_option="RAW")

    print(f"CSV file: {csv_file}")
    print(f"Worksheet: {WORKSHEET_NAME}")
    print(f"Header written: {header_written}")
    print(f"Rows in CSV: {len(data_rows)}")
    print(f"Existing keys: {len(existing_keys)}")
    print(f"New rows appended: {len(new_rows)}")
    print(f"Duplicates skipped: {len(data_rows) - len(new_rows)}")
    return len(new_rows)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Append official congressional PTR rows to Google Sheets raw_congress_ptr."
    )
    parser.add_argument("--csv", type=Path, default=CSV_FILE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    append_csv_to_sheet(args.csv)
