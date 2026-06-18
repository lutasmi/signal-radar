import argparse
import csv
import os
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


CSV_FILE = Path("data/sec_form4_latest.csv")
CREDENTIALS_FILE = "credentials.json"
WORKSHEET_NAME = "raw_sec_form4"
UNIQUE_KEY_COLUMNS = ["source_url"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def read_csv(csv_file: Path):
    if not csv_file.exists():
        raise FileNotFoundError(f"CSV not found: {csv_file}")

    with csv_file.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        raise ValueError(f"CSV is empty: {csv_file}")

    header = rows[0]
    data_rows = rows[1:]
    return header, data_rows


def open_raw_signals_worksheet(header, data_rows):
    load_dotenv()

    sheets_id = os.getenv("GOOGLE_SHEETS_ID", "")
    if not sheets_id:
        raise ValueError("GOOGLE_SHEETS_ID not found in environment")

    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheets_id)
    try:
        return sheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        rows = max(len(data_rows) + 1, 1)
        cols = max(len(header), 1)
        return sheet.add_worksheet(title=WORKSHEET_NAME, rows=rows, cols=cols)


def normalize_row(row, width):
    return row + [""] * (width - len(row))


def build_key(header, row):
    missing_columns = [column for column in UNIQUE_KEY_COLUMNS if column not in header]
    if missing_columns:
        raise ValueError(f"Missing key columns: {missing_columns}")

    normalized_row = normalize_row(row, len(header))
    return tuple(normalized_row[header.index(column)].strip() for column in UNIQUE_KEY_COLUMNS)


def build_existing_keys(values, csv_header):
    if len(values) <= 1:
        return set()

    header = values[0] if values[0] == csv_header else csv_header
    return {build_key(header, row) for row in values[1:]}


def filter_new_rows(header, data_rows, existing_keys):
    new_rows = []
    seen_keys = set(existing_keys)

    for row in data_rows:
        key = build_key(header, row)
        if key in seen_keys:
            continue

        seen_keys.add(key)
        new_rows.append(row)

    return new_rows


def append_csv_to_sheet(csv_file: Path):
    header, data_rows = read_csv(csv_file)
    worksheet = open_raw_signals_worksheet(header, data_rows)
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
        description="Append SEC Form 4 CSV rows to Google Sheets raw_sec_form4."
    )
    parser.add_argument("--csv", type=Path, default=CSV_FILE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    append_csv_to_sheet(args.csv)
