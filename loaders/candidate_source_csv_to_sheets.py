import glob
from pathlib import Path

import gspread

from radar import loaders
from radar.sheets import open_sheet


def latest_processed_csv(source_name):
    pattern = Path("data") / "processed" / source_name / f"{source_name}_*.csv"
    matches = sorted(glob.glob(str(pattern)))
    if not matches:
        raise FileNotFoundError(f"No processed CSV found for {source_name}: {pattern}")
    return Path(matches[-1])


def read_csv(csv_file):
    return loaders.read_csv(csv_file)


def open_raw_worksheet(worksheet_name, header, data_rows):
    sheet = open_sheet()
    try:
        return sheet.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        rows = max(len(data_rows) + 1, 1)
        cols = max(len(header), 1)
        return sheet.add_worksheet(title=worksheet_name, rows=rows, cols=cols)


def build_key(header, row, unique_key_columns):
    return loaders.build_key(header, row, unique_key_columns)


def build_existing_keys(values, csv_header, unique_key_columns):
    return loaders.build_existing_keys(values, csv_header, unique_key_columns)


def filter_new_rows(header, data_rows, existing_keys, unique_key_columns):
    return loaders.filter_new_rows(header, data_rows, existing_keys, unique_key_columns)


def append_csv_to_sheet(csv_file, worksheet_name, unique_key_columns):
    header, data_rows = read_csv(csv_file)
    worksheet = open_raw_worksheet(worksheet_name, header, data_rows)
    existing_values = worksheet.get_all_values()
    existing_keys = build_existing_keys(existing_values, header, unique_key_columns)
    new_rows = filter_new_rows(header, data_rows, existing_keys, unique_key_columns)

    header_written = False
    if not existing_values:
        worksheet.append_row(header, value_input_option="USER_ENTERED")
        header_written = True

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")

    print(f"CSV file: {csv_file}")
    print(f"Worksheet: {worksheet_name}")
    print(f"Header written: {header_written}")
    print(f"Rows in CSV: {len(data_rows)}")
    print(f"Existing keys: {len(existing_keys)}")
    print(f"New rows appended: {len(new_rows)}")
    print(f"Duplicates skipped: {len(data_rows) - len(new_rows)}")

    if new_rows:
        print("Example row appended:")
        print(dict(zip(header, new_rows[0])))

    return len(new_rows), new_rows[0] if new_rows else []
