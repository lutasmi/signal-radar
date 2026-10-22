import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

from loaders import candidate_source_csv_to_sheets as base


SOURCE_NAME = "sec_edgar_additional"
WORKSHEET_NAME = "raw_sec_edgar_additional"
UNIQUE_KEY_COLUMNS = ["form_type", "accession"]


def read_csv(csv_file):
    return base.read_csv(csv_file)


def build_key(header, row):
    return base.build_key(header, row, UNIQUE_KEY_COLUMNS)


def build_existing_keys(values, csv_header):
    return base.build_existing_keys(values, csv_header, UNIQUE_KEY_COLUMNS)


def filter_new_rows(header, data_rows, existing_keys):
    return base.filter_new_rows(header, data_rows, existing_keys, UNIQUE_KEY_COLUMNS)


def append_csv_to_sheet(csv_file=None):
    csv_file = csv_file or base.latest_processed_csv(SOURCE_NAME)
    return base.append_csv_to_sheet(csv_file, WORKSHEET_NAME, UNIQUE_KEY_COLUMNS)


def parse_args():
    parser = argparse.ArgumentParser(description="Append additional SEC EDGAR CSV rows to Google Sheets.")
    parser.add_argument("--csv", type=Path, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    append_csv_to_sheet(args.csv)
