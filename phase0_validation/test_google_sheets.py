import gspread
import os
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from datetime import datetime

load_dotenv()

SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
CREDENTIALS_FILE = "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

try:
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)

    sheet = client.open_by_key(SHEETS_ID)

    print(f"OK - Sheet abierta: {sheet.title}")

    tabs = {
        "raw_signals": [
            "signal_id", "signal_type", "ticker", "person_name",
            "person_type", "party", "chamber", "committee", "action",
            "amount_min", "amount_max", "transaction_date", "filing_date",
            "filing_delay_days", "source", "source_url", "created_at"
        ],
        "ticker_scores": [
            "computed_date", "ticker", "score", "signal_count",
            "families_present", "signals_detail", "alert_level",
            "alert_sent", "notes"
        ],
        "alerts_log": [
            "alert_id", "sent_at", "ticker", "score", "alert_level",
            "signal_summary", "price_at_alert",
            "price_5d_later", "price_10d_later", "outcome_pct_10d"
        ],
        "source_log": [
            "run_at", "source", "status", "records_fetched",
            "records_new", "error_msg"
        ]
    }

    existing_tabs = [ws.title for ws in sheet.worksheets()]

    for tab_name, headers in tabs.items():
        if tab_name in existing_tabs:
            ws = sheet.worksheet(tab_name)
            print(f"OK - pestaña ya existe: {tab_name}")
        else:
            ws = sheet.add_worksheet(title=tab_name, rows=1000, cols=len(headers))
            ws.append_row(headers)
            print(f"CREADA - pestaña: {tab_name}")

    ws = sheet.worksheet("source_log")
    ws.append_row([
        datetime.utcnow().isoformat(),
        "google_sheets_test",
        "ok",
        1,
        1,
        ""
    ])

    print("OK - fila de prueba escrita en source_log")

except Exception as e:
    print(f"ERROR: {e}")