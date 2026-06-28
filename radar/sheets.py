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


def replace_worksheet(sheet, worksheet_name, header, records):
    worksheet = open_or_create_worksheet(
        sheet=sheet,
        worksheet_name=worksheet_name,
        row_count=len(records),
        column_count=len(header),
    )
    rows = [header]
    rows.extend([[record[column] for column in header] for record in records])

    worksheet.clear()
    if rows:
        worksheet.update(rows, value_input_option="RAW")
