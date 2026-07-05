import argparse
from collections import Counter
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

from radar.dates import parse_date
from radar.records import get_value
from radar.sheets import open_sheet
from scripts import build_signals_sheet


QUALITY_SPECS = {
    build_signals_sheet.RAW_CAPITOL_TRADES: {
        "key_columns": [
            "politician",
            "transaction_date",
            "asset_name",
            "trade_type",
            "amount",
        ],
        "critical_columns": [
            "politician",
            "asset_name",
            "transaction_date",
            "trade_type",
            "amount",
            "source_url",
        ],
        "date_columns": ["transaction_date"],
    },
    build_signals_sheet.RAW_SEC_FORM4: {
        "key_columns": [
            "source_url",
            "ticker",
            "insider_name",
            "transaction_date",
            "transaction_code",
            "acquired_disposed",
            "shares",
            "price",
        ],
        "critical_columns": [
            "ticker",
            "issuer_name",
            "insider_name",
            "transaction_date",
            "transaction_code",
            "acquired_disposed",
            "shares",
            "source_url",
        ],
        "date_columns": ["transaction_date", "filing_date"],
    },
    build_signals_sheet.RAW_USASPENDING: {
        "key_columns": ["award_id"],
        "critical_columns": [
            "award_id",
            "recipient_name",
            "award_amount",
            "period_of_performance_start_date",
            "source_url",
        ],
        "date_columns": ["period_of_performance_start_date"],
    },
}


def row_key(row, columns):
    return tuple(get_value(row, column) for column in columns)


def exact_row_key(row, header):
    return tuple(get_value(row, column) for column in header)


def duplicate_count(keys):
    counts = Counter(keys)
    return sum(count - 1 for count in counts.values() if count > 1)


def invalid_dates(rows, date_columns):
    invalid = []
    for index, row in enumerate(rows, start=1):
        for column in date_columns:
            value = get_value(row, column)
            if value and parse_date(value) is None:
                invalid.append((index, column, value))
    return invalid


def blank_counts(rows, columns):
    return {
        column: sum(1 for row in rows if not get_value(row, column))
        for column in columns
    }


def validate_source(worksheet_name, rows):
    spec = QUALITY_SPECS[worksheet_name]
    header = build_signals_sheet.RAW_HEADERS[worksheet_name]
    failures = []

    exact_duplicates = duplicate_count(exact_row_key(row, header) for row in rows)
    logical_duplicates = duplicate_count(
        row_key(row, spec["key_columns"]) for row in rows
    )
    blanks = blank_counts(rows, spec["critical_columns"])
    bad_dates = invalid_dates(rows, spec["date_columns"])

    if exact_duplicates:
        failures.append(f"exact duplicate rows: {exact_duplicates}")
    if logical_duplicates:
        failures.append(f"logical duplicate rows: {logical_duplicates}")

    blank_failures = {
        column: count for column, count in blanks.items() if count
    }
    if blank_failures:
        failures.append(f"blank critical values: {blank_failures}")

    if bad_dates:
        failures.append(f"invalid dates: {bad_dates[:10]}")

    if worksheet_name == build_signals_sheet.RAW_SEC_FORM4:
        invalid_transactions = [
            index
            for index, row in enumerate(rows, start=1)
            if get_value(row, "transaction_code") != "P"
            or get_value(row, "acquired_disposed") != "A"
        ]
        if invalid_transactions:
            failures.append(
                f"non-purchase Form 4 rows: {invalid_transactions[:10]}"
            )

    if worksheet_name == build_signals_sheet.RAW_CAPITOL_TRADES:
        blank_tickers = sum(1 for row in rows if not get_value(row, "ticker"))
        print(f"- blank ticker rows: {blank_tickers}")

    print(f"- rows: {len(rows)}")
    print(f"- exact duplicate rows: {exact_duplicates}")
    print(f"- logical duplicate rows: {logical_duplicates}")
    print(f"- blank critical values: {blank_failures or {}}")
    print(f"- invalid dates: {len(bad_dates)}")

    return failures


def validate_raw_quality():
    sheet = open_sheet()
    all_failures = {}

    for worksheet_name in QUALITY_SPECS:
        print(f"\n== {worksheet_name} ==")
        rows = build_signals_sheet.read_records(sheet, worksheet_name)
        failures = validate_source(worksheet_name, rows)
        if failures:
            all_failures[worksheet_name] = failures

    if all_failures:
        for worksheet_name, failures in all_failures.items():
            print(f"\nFAIL {worksheet_name}")
            for failure in failures:
                print(f"- {failure}")
        raise ValueError("Raw data quality validation failed")

    print("\nRaw data quality validation passed")
    return True


def parse_args():
    return argparse.ArgumentParser(
        description="Validate raw Google Sheets source data quality without writing."
    ).parse_args()


def main():
    parse_args()
    try:
        validate_raw_quality()
    except Exception as exc:
        print(f"\nVALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
