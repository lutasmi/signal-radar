import argparse
import csv
import os
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


CSV_FILE = Path("data/capitol_trades_latest.csv")
CREDENTIALS_FILE = "credentials.json"
WORKSHEET_NAME = "raw_signals"

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


def open_raw_signals_worksheet():
    load_dotenv()

    sheets_id = os.getenv("GOOGLE_SHEETS_ID", "")
    if not sheets_id:
        raise ValueError("GOOGLE_SHEETS_ID not found in environment")

    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheets_id)
    return sheet.worksheet(WORKSHEET_NAME)


def worksheet_is_empty(worksheet) -> bool:
    return len(worksheet.get_all_values()) == 0


def append_csv_to_sheet(csv_file: Path):
    header, data_rows = read_csv(csv_file)
    worksheet = open_raw_signals_worksheet()

    header_written = False
    if worksheet_is_empty(worksheet):
        worksheet.append_row(header, value_input_option="USER_ENTERED")
        header_written = True

    if data_rows:
        worksheet.append_rows(data_rows, value_input_option="USER_ENTERED")

    print(f"CSV file: {csv_file}")
    print(f"Worksheet: {WORKSHEET_NAME}")
    print(f"Header written: {header_written}")
    print(f"Rows appended: {len(data_rows)}")

    if data_rows:
        print("Example row appended:")
        print(dict(zip(header, data_rows[0])))

    return len(data_rows), data_rows[0] if data_rows else []


def parse_args():
    parser = argparse.ArgumentParser(
        description="Append Capitol Trades CSV rows to Google Sheets raw_signals."
    )
    parser.add_argument("--csv", type=Path, default=CSV_FILE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    append_csv_to_sheet(args.csv)
