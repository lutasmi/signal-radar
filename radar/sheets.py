import os

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

from radar.records import rows_to_dicts


CREDENTIALS_FILE = "credentials.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def open_sheet():
    load_dotenv()

    sheets_id = os.getenv("GOOGLE_SHEETS_ID", "")
    if not sheets_id:
        raise ValueError("GOOGLE_SHEETS_ID not found in environment")

    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheets_id)


def read_records(sheet, worksheet_name):
    worksheet = sheet.worksheet(worksheet_name)
    return rows_to_dicts(worksheet.get_all_values())


def open_or_create_worksheet(sheet, worksheet_name, row_count, column_count):
    try:
        return sheet.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        return sheet.add_worksheet(
            title=worksheet_name,
            rows=max(row_count + 1, 1),
            cols=column_count,
        )


WRITE_CHUNK_ROWS = 5000


def write_rows(worksheet, start_row, rows):
    """Write rows at an explicit A1 position.

    Never uses the Sheets "append" API: its table detection can shift rows to
    other columns when the first row has blank cells (this corrupted
    raw_congress_ptr once). The grid is grown first so large writes fit.
    """
    if not rows:
        return
    last_row = start_row + len(rows) - 1
    width = max(len(row) for row in rows)
    if worksheet.row_count < last_row:
        worksheet.add_rows(last_row - worksheet.row_count)
    if worksheet.col_count < width:
        worksheet.add_cols(width - worksheet.col_count)
    for offset in range(0, len(rows), WRITE_CHUNK_ROWS):
        worksheet.update(
            values=rows[offset:offset + WRITE_CHUNK_ROWS],
            range_name=f"A{start_row + offset}",
            value_input_option="RAW",
        )


def replace_worksheet(sheet, worksheet_name, header, records):
    worksheet = open_or_create_worksheet(
        sheet=sheet,
        worksheet_name=worksheet_name,
        row_count=len(records),
        column_count=len(header),
    )
    rows = [header]
    rows.extend([[record.get(column, "") for column in header] for record in records])

    worksheet.clear()
    write_rows(worksheet, 1, rows)


def append_records(sheet, worksheet_name, header, records):
    """Append records to a log-style worksheet, writing the header if missing."""
    if not records:
        return
    worksheet = open_or_create_worksheet(sheet, worksheet_name, len(records), len(header))
    append_rows(worksheet, header, [[record.get(column, "") for column in header] for record in records])


def append_rows(worksheet, header, rows):
    """Append raw rows below the existing data, guaranteeing the header in row 1."""
    values = worksheet.get_all_values()
    if not values:
        write_rows(worksheet, 1, [header] + rows)
        return
    if values[0] != header:
        worksheet.insert_row(header, 1, value_input_option="RAW")
        values = [header] + values
    write_rows(worksheet, len(values) + 1, rows)


def read_records_or_empty(sheet, worksheet_name):
    import gspread

    try:
        return read_records(sheet, worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        return []
