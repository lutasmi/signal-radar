import os
from collections import Counter
from datetime import date, datetime
from hashlib import sha256

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


CREDENTIALS_FILE = "credentials.json"
OUTPUT_WORKSHEET_NAME = "signals"

RAW_CAPITOL_TRADES = "raw_capitol_trades"
RAW_SEC_FORM4 = "raw_sec_form4"
RAW_USASPENDING = "raw_usaspending"

RAW_HEADERS = {
    RAW_CAPITOL_TRADES: [
        "politician",
        "asset_name",
        "ticker",
        "published",
        "transaction_date",
        "filing_delay",
        "owner",
        "trade_type",
        "amount",
        "price",
        "source",
        "source_url",
    ],
    RAW_SEC_FORM4: [
        "ticker",
        "issuer_name",
        "insider_name",
        "insider_title",
        "transaction_date",
        "transaction_code",
        "acquired_disposed",
        "shares",
        "price",
        "estimated_value",
        "filing_date",
        "source_url",
    ],
    RAW_USASPENDING: [
        "award_id",
        "recipient_name",
        "awarding_agency_name",
        "award_amount",
        "period_of_performance_start_date",
        "description",
        "source_url",
    ],
}

SIGNALS_HEADER = [
    "signal_id",
    "signal_date",
    "signal_type",
    "ticker",
    "entity_name",
    "actor_name",
    "actor_type",
    "amount",
    "source",
    "source_url",
    "raw_key",
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

MONTHS = {
    "jan": 1,
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "ago": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "dic": 12,
}

DATE_WARNINGS = []


def open_sheet():
    load_dotenv()

    sheets_id = os.getenv("GOOGLE_SHEETS_ID", "")
    if not sheets_id:
        raise ValueError("GOOGLE_SHEETS_ID not found in environment")

    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheets_id)


def get_value(row, column):
    value = row.get(column, "")
    if value is None:
        return ""
    return str(value).strip()


def make_signal_id(source, raw_key):
    digest = sha256(f"{source}|{raw_key}".encode("utf-8")).hexdigest()[:16]
    return f"{source}_{digest}"


def normalize_signal_date(value):
    original_value = value
    value = value.strip()
    if not value:
        return value

    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass

    parts = value.replace(",", " ").split()
    if len(parts) == 3:
        day_text, month_text, year_text = parts
        month = MONTHS.get(month_text[:3].lower())
        if month:
            try:
                return date(int(year_text), month, int(day_text)).isoformat()
            except ValueError:
                pass

    print(f"WARNING: could not parse signal_date: {original_value}")
    DATE_WARNINGS.append(original_value)
    return original_value


def build_signal(
    signal_date,
    signal_type,
    ticker,
    entity_name,
    actor_name,
    actor_type,
    amount,
    source,
    source_url,
    raw_key,
):
    return {
        "signal_id": make_signal_id(source, raw_key),
        "signal_date": normalize_signal_date(signal_date),
        "signal_type": signal_type,
        "ticker": ticker,
        "entity_name": entity_name,
        "actor_name": actor_name,
        "actor_type": actor_type,
        "amount": amount,
        "source": source,
        "source_url": source_url,
        "raw_key": raw_key,
    }


def normalize_capitol_trade(row):
    trade_type = get_value(row, "trade_type").lower()
    if trade_type == "buy":
        signal_type = "PTR_BUY"
    elif trade_type == "sell":
        signal_type = "PTR_SELL"
    else:
        signal_type = "PTR_OTHER"

    raw_key = "".join(
        [
            get_value(row, "politician"),
            get_value(row, "transaction_date"),
            get_value(row, "asset_name"),
            get_value(row, "trade_type"),
            get_value(row, "amount"),
        ]
    )

    return build_signal(
        signal_date=get_value(row, "transaction_date"),
        signal_type=signal_type,
        ticker=get_value(row, "ticker"),
        entity_name=get_value(row, "asset_name"),
        actor_name=get_value(row, "politician"),
        actor_type="politician",
        amount=get_value(row, "amount"),
        source="capitol_trades",
        source_url=get_value(row, "source_url"),
        raw_key=raw_key,
    )


def normalize_sec_form4(row):
    raw_key = get_value(row, "source_url")
    return build_signal(
        signal_date=get_value(row, "transaction_date"),
        signal_type="INSIDER_BUY",
        ticker=get_value(row, "ticker"),
        entity_name=get_value(row, "issuer_name"),
        actor_name=get_value(row, "insider_name"),
        actor_type="insider",
        amount=get_value(row, "estimated_value"),
        source="sec_form4",
        source_url=get_value(row, "source_url"),
        raw_key=raw_key,
    )


def normalize_usaspending(row):
    raw_key = get_value(row, "award_id")
    return build_signal(
        signal_date=get_value(row, "period_of_performance_start_date"),
        signal_type="CONTRACT",
        ticker="",
        entity_name=get_value(row, "recipient_name"),
        actor_name=get_value(row, "awarding_agency_name"),
        actor_type="agency",
        amount=get_value(row, "award_amount"),
        source="usaspending",
        source_url=get_value(row, "source_url"),
        raw_key=raw_key,
    )


def read_records(sheet, worksheet_name):
    worksheet = sheet.worksheet(worksheet_name)
    values = worksheet.get_all_values()
    if not values:
        return []

    expected_header = RAW_HEADERS[worksheet_name]
    if set(expected_header).issubset(set(values[0])):
        header = values[0]
        data_rows = values[1:]
    else:
        header = expected_header
        data_rows = values

    records = []
    for data_row in data_rows:
        record = {}
        for index, column in enumerate(header):
            if not column or column in record:
                continue

            record[column] = data_row[index] if index < len(data_row) else ""

        records.append(record)

    return records


def build_signals(sheet):
    source_specs = [
        (RAW_CAPITOL_TRADES, normalize_capitol_trade),
        (RAW_SEC_FORM4, normalize_sec_form4),
        (RAW_USASPENDING, normalize_usaspending),
    ]

    signals = []
    seen_raw_keys = set()

    for worksheet_name, normalize in source_specs:
        for row in read_records(sheet, worksheet_name):
            signal = normalize(row)
            raw_key = signal["raw_key"]
            if raw_key in seen_raw_keys:
                continue

            seen_raw_keys.add(raw_key)
            signals.append(signal)

    return signals


def open_or_create_signals_worksheet(sheet, row_count):
    try:
        return sheet.worksheet(OUTPUT_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        rows = max(row_count + 1, 1)
        cols = len(SIGNALS_HEADER)
        return sheet.add_worksheet(
            title=OUTPUT_WORKSHEET_NAME,
            rows=rows,
            cols=cols,
        )


def write_signals(sheet, signals):
    worksheet = open_or_create_signals_worksheet(sheet, len(signals))
    rows = [SIGNALS_HEADER]
    rows.extend([[signal[column] for column in SIGNALS_HEADER] for signal in signals])

    worksheet.clear()
    if rows:
        worksheet.update(rows, value_input_option="USER_ENTERED")


def print_summary(signals):
    counts = Counter(signal["signal_type"] for signal in signals)

    print(f"Worksheet rebuilt: {OUTPUT_WORKSHEET_NAME}")
    print(f"Signals generated: {len(signals)}")
    print(f"Date warnings: {len(DATE_WARNINGS)}")
    print("Count by signal_type:")
    for signal_type, count in sorted(counts.items()):
        print(f"- {signal_type}: {count}")

    print("Example 5 rows:")
    for signal in signals[:5]:
        print({column: signal[column] for column in SIGNALS_HEADER})


def main():
    sheet = open_sheet()
    signals = build_signals(sheet)
    write_signals(sheet, signals)
    print_summary(signals)


if __name__ == "__main__":
    main()
