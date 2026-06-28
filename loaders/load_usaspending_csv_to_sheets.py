import argparse
import sys
from pathlib import Path

import gspread

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar import loaders
from radar.sheets import open_sheet

CSV_FILE = Path("data/usaspending_latest.csv")
WORKSHEET_NAME = "raw_usaspending"
UNIQUE_KEY_COLUMNS = ["award_id"]

def read_csv(csv_file: Path):
    return loaders.read_csv(csv_file)


def open_raw_worksheet(header, data_rows):
    sheet = open_sheet()
    try:
        return sheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        rows = max(len(data_rows) + 1, 1)
        cols = max(len(header), 1)
        return sheet.add_worksheet(title=WORKSHEET_NAME, rows=rows, cols=cols)


def build_key(header, row):
    return loaders.build_key(header, row, UNIQUE_KEY_COLUMNS)


def build_existing_keys(values, csv_header):
    return loaders.build_existing_keys(values, csv_header, UNIQUE_KEY_COLUMNS)


def filter_new_rows(header, data_rows, existing_keys):
    return loaders.filter_new_rows(header, data_rows, existing_keys, UNIQUE_KEY_COLUMNS)


def append_csv_to_sheet(csv_file: Path):
    header, data_rows = read_csv(csv_file)
    worksheet = open_raw_worksheet(header, data_rows)
    existing_values = worksheet.get_all_values()
    existing_keys = build_existing_keys(existing_values, header)
    new_rows = filter_new_rows(header, data_rows, existing_keys)

    header_written = False
    if not existing_values:
        worksheet.append_row(header, value_input_option="USER_ENTERED")
        header_written = True

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")

    print(f"CSV file: {csv_file}")
    print(f"Worksheet: {WORKSHEET_NAME}")
    print(f"Header written: {header_written}")
    print(f"Rows in CSV: {len(data_rows)}")
    print(f"Existing keys: {len(existing_keys)}")
    print(f"New rows appended: {len(new_rows)}")
    print(f"Duplicates skipped: {len(data_rows) - len(new_rows)}")

    if new_rows:
        print("Example row appended:")
        print(dict(zip(header, new_rows[0])))

    return len(new_rows), new_rows[0] if new_rows else []


def parse_args():
    parser = argparse.ArgumentParser(
        description="Append USASpending CSV rows to Google Sheets raw_usaspending."
    )
    parser.add_argument("--csv", type=Path, default=CSV_FILE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    append_csv_to_sheet(args.csv)
