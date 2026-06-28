from collections import Counter

from radar.dates import normalize_date
from radar.records import get_value, stable_id
from radar.sheets import open_sheet, replace_worksheet


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

DATE_WARNINGS = []


def make_signal_id(source, raw_key):
    return stable_id(source, [source, raw_key])


def normalize_signal_date(value):
    normalized_value = normalize_date(value, DATE_WARNINGS)
    if normalized_value == value.strip() and value.strip() in DATE_WARNINGS:
        print(f"WARNING: could not parse signal_date: {value}")
    return normalized_value


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
    raw_key = "|".join(
        [
            get_value(row, "source_url"),
            get_value(row, "ticker"),
            get_value(row, "insider_name"),
            get_value(row, "transaction_date"),
            get_value(row, "transaction_code"),
            get_value(row, "acquired_disposed"),
            get_value(row, "shares"),
            get_value(row, "price"),
        ]
    )
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
            source_raw_key = (signal["source"], signal["raw_key"])
            if source_raw_key in seen_raw_keys:
                continue

            seen_raw_keys.add(source_raw_key)
            signals.append(signal)

    return signals


def write_signals(sheet, signals):
    replace_worksheet(sheet, OUTPUT_WORKSHEET_NAME, SIGNALS_HEADER, signals)


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
